from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from storyboardctl.comfy.client import ComfyClient
from storyboardctl.comfy.zimage import ZImageContext, ZImageTurboAdapter
from storyboardctl.errors import Conflict, ExternalServiceFailure
from storyboardctl.paths import resolve_project_path
from storyboardctl.service import StoryboardService


class ImageComfyInterface(Protocol):
    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str: ...
    def wait_for_completion(self, prompt_id: str, *, timeout_seconds: float, poll_seconds: float) -> dict[str, Any]: ...
    def download_image(self, history_entry: dict[str, Any], destination: Path) -> Path: ...


class BoardRunner:
    def __init__(
        self, service: StoryboardService, comfy: ImageComfyInterface, adapter: ZImageTurboAdapter | None = None
    ) -> None:
        self.service = service
        self.comfy = comfy
        self.adapter = adapter or ZImageTurboAdapter()

    def execute(self, frame_id: str, *, timeout_seconds: float = 900, poll_seconds: float = 2) -> dict[str, Any]:
        details = self.service.board_frame_details(frame_id)
        if details["state"] != "planned":
            raise Conflict(f"board frame must be planned before execution: {details['state']}")
        self.service.transition_board_frame(frame_id, "submitting")
        settings = details["settings"]
        context = ZImageContext(
            prompt=details["prompt_snapshot"],
            width=int(settings.get("width", 1344)),
            height=int(settings.get("height", 768)),
            steps=int(settings.get("steps", 8)),
            seed=int(details["seed"]),
            output_key=Path(details["output_path"]).stem,
        )
        try:
            workflow = self.adapter.build_workflow(context)
            workflow_path = resolve_project_path(self.service.project_root, details["workflow_path"])
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            prompt_id = self.comfy.enqueue(workflow)
            self.service.transition_board_frame(frame_id, "queued", comfy_prompt_id=prompt_id)
            self.service.transition_board_frame(frame_id, "running")
            history = self.comfy.wait_for_completion(
                prompt_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
            )
            self.comfy.download_image(history, resolve_project_path(self.service.project_root, details["output_path"]))
            return self.service.complete_board_frame(frame_id)
        except Exception as error:
            current = self.service.board_frame_details(frame_id)
            if current["state"] in ("submitting", "queued", "running", "timed_out"):
                self.service.transition_board_frame(frame_id, "failed", error_message=str(error))
            if isinstance(error, (Conflict, ExternalServiceFailure)):
                raise
            raise ExternalServiceFailure(f"rapid-board frame failed: {error}") from error


def preflight(client: ComfyClient, adapter: ZImageTurboAdapter | None = None) -> dict[str, Any]:
    requirements = (adapter or ZImageTurboAdapter()).requirements()
    return client.preflight(nodes=requirements["nodes"], models=requirements["models"])
