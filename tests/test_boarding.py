from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from storyboardctl.boarding import BoardRunner
from storyboardctl.database import Database
from storyboardctl.models import DialogueCueSpec, ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.preview import PreviewBuilder, PreviewSettings, ass_document, ffmpeg_has_subtitles
from storyboardctl.service import StoryboardService


class FakeComfy:
    def enqueue(self, workflow: dict[str, dict[str, Any]]) -> str:
        assert workflow["10"]["class_type"] == "SaveImage"
        return "image-job"

    def wait_for_completion(self, prompt_id: str, *, timeout_seconds: float, poll_seconds: float) -> dict[str, Any]:
        return {"outputs": {"10": {"images": [{"filename": "frame.png"}]}}}

    def download_image(self, history_entry: dict[str, Any], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")
        return destination


def _service(tmp_path: Path) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="rapid",
            title="Rapid",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="s1",
                        title="Arrival",
                        description="Two friends arrive at a colorful theme park.",
                        prompt="Full video prompt",
                        duration_seconds=3,
                        dialogue=[
                            DialogueCueSpec(
                                speaker="Alice", speaker_id="S1", text="We made it!", start_seconds=0.5, end_seconds=1.8
                            )
                        ],
                    )
                ],
            ),
        )
    )
    return service


def test_board_frame_is_versioned_and_runner_persists_provenance(tmp_path) -> None:
    service = _service(tmp_path)
    planned = service.plan_board_frame("v1", 10, seed=7)
    assert "Two friends arrive" in planned["prompt_snapshot"]
    assert "Full video prompt" not in planned["prompt_snapshot"]
    assert planned["dialogue"][0]["text"] == "We made it!"

    completed = BoardRunner(service, FakeComfy()).execute(planned["frame_id"], poll_seconds=0)
    assert completed["state"] == "completed"
    assert completed["output_sha256"]
    assert service.latest_board_frames("v1")[0]["frame_id"] == planned["frame_id"]
    assert service.plan_board_frame("v1", 10)["attempt_number"] == 2


def test_ass_subtitles_use_exact_dialogue_timing() -> None:
    document = ass_document(
        [
            {"start_seconds": 1.25, "end_seconds": 2.5, "text": "Hello {there}"},
        ],
        1280,
        720,
    )
    assert "Dialogue: 0,0:00:01.25,0:00:02.50" in document
    assert r"Hello \{there\}" in document


def test_preview_builder_creates_animated_subtitled_video(tmp_path) -> None:
    if shutil.which("ffmpeg") is None or not ffmpeg_has_subtitles():
        return
    service = _service(tmp_path)
    planned = service.plan_board_frame("v1", 10, seed=7, settings={"width": 320, "height": 180})
    image = tmp_path / planned["output_path"]
    image.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180", "-frames:v", "1", str(image)],
        check=True,
        capture_output=True,
    )
    service.transition_board_frame(planned["frame_id"], "submitting")
    service.transition_board_frame(planned["frame_id"], "queued", comfy_prompt_id="local")
    service.complete_board_frame(planned["frame_id"])

    preview = PreviewBuilder(service).build("v1", PreviewSettings(width=320, height=180, fps=8))
    assert preview["state"] == "completed"
    assert (tmp_path / preview["output_path"]).stat().st_size > 0
    assert (tmp_path / preview["manifest_path"]).is_file()
