from __future__ import annotations

from pathlib import Path
from typing import Any

from storyboardctl.comfy.adapters import WorkflowContext
from storyboardctl.database import Database
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


class FakeComfy:
    def __init__(self) -> None:
        self.workflow: dict[str, dict[str, Any]] | None = None

    def upload_image(self, image_path: Path) -> str:
        return f"uploaded-{image_path.name}"

    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str:
        self.workflow = workflow
        return "prompt-1"

    def wait_for_completion(self, prompt_id: str, **_: Any) -> dict[str, Any]:
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
    assert fake_comfy.workflow["1"]["inputs"]["references"] == ["uploaded-reference.png"]
    assert (tmp_path / planned["workflow_path"]).exists()
    assert (tmp_path / planned["output_path"]).read_bytes() == b"completed-video"
