from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol, cast

from storyboardctl.comfy.adapters import WorkflowAdapter, WorkflowContext
from storyboardctl.compiler import probe_duration
from storyboardctl.errors import Conflict, ExternalServiceFailure, NotFound, StoryboardError
from storyboardctl.models import RenderMode
from storyboardctl.paths import resolve_project_path
from storyboardctl.service import StoryboardService


class ComfyInterface(Protocol):
    def upload_image(self, image_path: Path, *, remote_name: str | None = None) -> str: ...

    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str: ...

    def wait_for_completion(self, prompt_id: str, *, timeout_seconds: float, poll_seconds: float) -> dict[str, Any]: ...

    def download(self, history_entry: dict[str, Any], destination: Path) -> Path: ...


def h3_frames_for_duration(duration_seconds: float, fps: int = 24) -> int:
    blocks = max(1, math.ceil((duration_seconds * fps - 5) / 17))
    return 17 * blocks + 5


class RenderRunner:
    def __init__(
        self,
        service: StoryboardService,
        comfy: ComfyInterface,
        adapters: dict[str, WorkflowAdapter],
    ) -> None:
        self.service = service
        self.comfy = comfy
        self.adapters = adapters

    def execute(
        self,
        render_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_seconds: float = 3,
    ) -> dict[str, Any]:
        self.submit(render_id)
        return self.wait(render_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)

    @staticmethod
    def _error_context(details: dict[str, Any]) -> dict[str, Any]:
        return {
            key: details.get(key)
            for key in ("render_id", "attempt_number", "workflow_path", "output_path", "comfy_prompt_id")
        }

    def submit(self, render_id: str) -> dict[str, Any]:
        details = self.service.render_details(render_id)
        if details["state"] != "planned":
            raise Conflict(f"render must be planned before execution: {details['state']}")
        try:
            adapter = self.adapters[details["adapter"]]
        except KeyError as error:
            raise NotFound(f"workflow adapter not found: {details['adapter']}") from error

        # This transactional transition is the submission lease. If another worker
        # already claimed the render, it fails before either worker contacts ComfyUI.
        self.service.transition_render(render_id, "submitting")

        references: list[str] = []
        first_frame: str | None = None
        last_frame: str | None = None
        try:
            for asset in details["assets"]:
                verified = self.service.verify_asset(asset["asset_key"])
                absolute = resolve_project_path(self.service.project_root, asset["path"], must_exist=True)
                if asset["role"] not in ("reference", "first_frame", "last_frame"):
                    continue
                if asset["kind"] != "image":
                    raise Conflict(f"asset role {asset['role']} requires an image: {asset['asset_key']}")
                remote_name = f"{verified['sha256'][:16]}-{absolute.name}"
                uploaded = self.comfy.upload_image(absolute, remote_name=remote_name)
                if asset["role"] == "reference":
                    references.append(uploaded)
                elif asset["role"] == "first_frame":
                    first_frame = uploaded
                elif asset["role"] == "last_frame":
                    last_frame = uploaded

            settings = details["settings"]
            context = WorkflowContext(
                prompt=details["prompt"],
                render_mode=RenderMode(details["render_mode"]),
                width=int(settings.get("width", 1344)),
                height=int(settings.get("height", 768)),
                frames=int(settings.get("frames", h3_frames_for_duration(details["intended_duration_seconds"]))),
                steps=int(settings.get("steps", 20)),
                seed=int(details["seed"]),
                output_key=Path(details["output_path"]).stem,
                reference_images=tuple(references),
                first_frame=first_frame,
                last_frame=last_frame,
            )
            workflow = self._workflow_for(details, adapter, context)
            snapshot = adapter.scrub_workflow(workflow)
            workflow_path = resolve_project_path(self.service.project_root, details["workflow_path"])
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            prompt_id = self.comfy.enqueue(workflow)
            return self.service.transition_render(render_id, "queued", comfy_prompt_id=prompt_id)
        except Exception as error:
            current = self.service.render_details(render_id)
            if current["state"] == "submitting":
                self.service.transition_render(render_id, "failed", error_message=str(error))
            context = self._error_context(current)
            if isinstance(error, StoryboardError):
                error.details = {**context, **error.details}
                raise
            raise ExternalServiceFailure(f"render submission failed: {error}", details=context) from error

    def wait(
        self,
        render_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_seconds: float = 3,
    ) -> dict[str, Any]:
        details = self.service.render_details(render_id)
        if details["state"] not in ("queued", "running", "timed_out"):
            raise Conflict(f"render cannot be waited for from state: {details['state']}")
        prompt_id = details.get("comfy_prompt_id")
        if not prompt_id:
            raise Conflict("render has no ComfyUI prompt ID to wait for")
        if details["state"] in ("queued", "timed_out"):
            self.service.transition_render(render_id, "running")
        try:
            history = self.comfy.wait_for_completion(
                prompt_id,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            output_path = resolve_project_path(self.service.project_root, details["output_path"])
            self.comfy.download(history, output_path)
            duration = probe_duration(output_path)
            return self.service.complete_render(render_id, duration_seconds=duration)
        except Exception as error:
            current = self.service.render_details(render_id)
            timed_out = isinstance(error, ExternalServiceFailure) and "timed out" in str(error).lower()
            if current["state"] in ("queued", "running", "timed_out"):
                next_state = "timed_out" if timed_out and current["comfy_prompt_id"] else "failed"
                if next_state != current["state"]:
                    self.service.transition_render(render_id, next_state, error_message=str(error))
            context = self._error_context(current)
            if isinstance(error, StoryboardError):
                error.details = {**context, **error.details}
                raise
            raise ExternalServiceFailure(f"render wait failed: {error}", details=context) from error

    def _workflow_for(
        self,
        details: dict[str, Any],
        adapter: WorkflowAdapter,
        context: WorkflowContext,
    ) -> dict[str, dict[str, Any]]:
        if details.get("replay_workflow") and details.get("source_render_id"):
            source = self.service.render_details(details["source_render_id"])
            source_path = resolve_project_path(self.service.project_root, source["workflow_path"], must_exist=True)
            workflow = cast(
                dict[str, dict[str, Any]],
                json.loads(source_path.read_text(encoding="utf-8")),
            )
            return adapter.prepare_replay(workflow, context)
        return adapter.build_workflow(context)

    def reconcile(
        self,
        render_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_seconds: float = 3,
    ) -> dict[str, Any]:
        return self.wait(render_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
