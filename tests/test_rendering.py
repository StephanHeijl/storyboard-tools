from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from storyboardctl.comfy.adapters import WorkflowContext
from storyboardctl.database import Database
from storyboardctl.errors import Conflict, ExternalServiceFailure
from storyboardctl.models import AssetKind, ProjectSpec, RenderMode, ShotSpec, StoryboardSpec
from storyboardctl.rendering import RenderRunner
from storyboardctl.service import StoryboardService


class FakeAdapter:
    name = "fake"

    def build_workflow(self, context: WorkflowContext) -> dict[str, dict[str, Any]]:
        return {
            "1": {
                "class_type": "FakeVideo",
                "inputs": {
                    "prompt": context.prompt,
                    "seed": context.seed,
                    "references": list(context.reference_images),
                },
            }
        }

    def scrub_workflow(self, workflow: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return workflow

    def prepare_replay(
        self, workflow: dict[str, dict[str, Any]], context: WorkflowContext
    ) -> dict[str, dict[str, Any]]:
        workflow["1"]["inputs"]["seed"] = context.seed
        return workflow


class FakeComfy:
    def __init__(self) -> None:
        self.workflow: dict[str, dict[str, Any]] | None = None
        self.uploaded_names: list[str] = []
        self.wait_calls = 0

    def upload_image(self, image_path: Path, *, remote_name: str | None = None) -> str:
        self.uploaded_names.append(remote_name or image_path.name)
        return f"uploaded-{remote_name or image_path.name}"

    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str:
        self.workflow = workflow
        return "prompt-1"

    def wait_for_completion(self, prompt_id: str, **_: Any) -> dict[str, Any]:
        self.wait_calls += 1
        return {"outputs": {"1": {"filename": "result.mp4"}}}

    def download(self, history_entry: dict[str, Any], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"completed-video")
        return destination


def test_runner_uploads_assets_snapshots_workflow_and_completes(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/reference.png").write_bytes(b"reference")
    service.import_spec(
        ProjectSpec(
            slug="runner",
            title="Runner",
            assets=[],
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="A safe prompt",
                        duration_seconds=1,
                        adapter="fake",
                        render_mode=RenderMode.reference_to_video,
                    )
                ],
            ),
        )
    )
    service.add_asset("reference", AssetKind.image, "assets/reference.png")
    service.link_asset("v1", 10, "reference", role="reference", order=0)
    planned = service.plan_render("v1", 10, seed=7, settings={"width": 320, "height": 180, "steps": 4})
    fake_comfy = FakeComfy()
    monkeypatch.setattr("storyboardctl.rendering.probe_duration", lambda _path: 1.1)

    result = RenderRunner(service, fake_comfy, {"fake": FakeAdapter()}).execute(planned["render_id"], poll_seconds=0)
    assert result["state"] == "completed"
    assert result["comfy_prompt_id"] == "prompt-1"
    assert fake_comfy.workflow is not None
    assert fake_comfy.workflow["1"]["inputs"]["references"][0].endswith("-reference.png")
    assert fake_comfy.uploaded_names[0] != "reference.png"
    assert (tmp_path / planned["workflow_path"]).exists()
    assert (tmp_path / planned["output_path"]).read_bytes() == b"completed-video"


def test_submit_returns_queued_without_waiting_and_wait_finishes(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="async",
            title="Async",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot", title="Shot", description="Shot", prompt="Shot", duration_seconds=1, adapter="fake"
                    )
                ],
            ),
        )
    )
    planned = service.plan_render("v1", 10, seed=1)
    comfy = FakeComfy()
    runner = RenderRunner(service, comfy, {"fake": FakeAdapter()})

    submitted = runner.submit(planned["render_id"])

    assert submitted["state"] == "queued"
    assert submitted["comfy_prompt_id"] == "prompt-1"
    assert comfy.wait_calls == 0
    monkeypatch.setattr("storyboardctl.rendering.probe_duration", lambda _path: 1.1)
    completed = runner.wait(planned["render_id"], poll_seconds=0)
    assert completed["state"] == "completed"
    assert comfy.wait_calls == 1


def test_timed_out_render_can_be_reconciled_without_new_submission(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="reconcile",
            title="Reconcile",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="Shot",
                        duration_seconds=1,
                        adapter="fake",
                    )
                ],
            ),
        )
    )
    planned = service.plan_render("v1", 10)
    comfy = FakeComfy()
    original_wait = comfy.wait_for_completion
    comfy.wait_for_completion = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ExternalServiceFailure("timed out waiting for ComfyUI prompt prompt-1")
    )
    runner = RenderRunner(service, comfy, {"fake": FakeAdapter()})
    with pytest.raises(ExternalServiceFailure, match="timed out") as captured:
        runner.execute(planned["render_id"], poll_seconds=0)
    assert captured.value.details["render_id"] == planned["render_id"]
    assert captured.value.details["attempt_number"] == 1
    assert captured.value.details["workflow_path"] == planned["workflow_path"]
    assert captured.value.details["output_path"] == planned["output_path"]
    assert service.render_details(planned["render_id"])["state"] == "timed_out"

    comfy.wait_for_completion = original_wait
    monkeypatch.setattr("storyboardctl.rendering.probe_duration", lambda _path: 1.1)
    result = runner.reconcile(planned["render_id"], poll_seconds=0)
    assert result["state"] == "completed"
    assert result["comfy_prompt_id"] == "prompt-1"


def test_execute_atomically_claims_a_planned_render(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="claim",
            title="Claim",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="Shot",
                        duration_seconds=1,
                        adapter="fake",
                    )
                ],
            ),
        )
    )
    planned = service.plan_render("v1", 10)
    service.transition_render(planned["render_id"], "submitting")
    comfy = FakeComfy()
    with pytest.raises(Conflict, match="planned"):
        RenderRunner(service, comfy, {"fake": FakeAdapter()}).execute(planned["render_id"])
    assert comfy.workflow is None


def test_reconcile_marks_non_timeout_failure_failed(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="failed-reconcile",
            title="Failed reconcile",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="Shot",
                        duration_seconds=1,
                        adapter="fake",
                    )
                ],
            ),
        )
    )
    planned = service.plan_render("v1", 10)
    service.transition_render(planned["render_id"], "queued", comfy_prompt_id="prompt-1")
    service.transition_render(planned["render_id"], "running")
    comfy = FakeComfy()
    comfy.wait_for_completion = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ExternalServiceFailure("ComfyUI execution failed")
    )

    with pytest.raises(ExternalServiceFailure, match="execution failed"):
        RenderRunner(service, comfy, {"fake": FakeAdapter()}).reconcile(planned["render_id"])

    details = service.render_details(planned["render_id"])
    assert details["state"] == "failed"
    assert details["error_message"] == "ComfyUI execution failed"


def test_wait_retries_finalization_claim_after_other_worker_releases(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="shared-wait",
            title="Shared wait",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="Shot",
                        duration_seconds=1,
                        adapter="fake",
                    )
                ],
            ),
        )
    )
    planned = service.plan_render("v1", 10)
    service.transition_render(planned["render_id"], "queued", comfy_prompt_id="prompt-1")
    service.transition_render(planned["render_id"], "running")
    assert service.claim_render_finalization(planned["render_id"], "worker-1") is True

    def release_during_poll(_seconds: float) -> None:
        service.release_render_finalization(planned["render_id"], "worker-1")

    monkeypatch.setattr("storyboardctl.rendering.time.sleep", release_during_poll)
    monkeypatch.setattr("storyboardctl.rendering.probe_duration", lambda _path: 1.1)
    comfy = FakeComfy()

    result = RenderRunner(service, comfy, {"fake": FakeAdapter()}).wait(
        planned["render_id"], timeout_seconds=1, poll_seconds=0
    )

    assert result["state"] == "completed"
    assert comfy.wait_calls == 1
