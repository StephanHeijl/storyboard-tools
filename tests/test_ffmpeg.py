from __future__ import annotations

import shutil
import subprocess

import pytest

from storyboardctl.compiler import CompilationSettings, Compiler, probe_duration
from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_build_assembles_two_approved_clips(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="movie",
            title="Movie",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key=f"shot-{index}",
                        title=f"Shot {index}",
                        description="Color",
                        prompt="Color",
                        duration_seconds=0.3,
                    )
                    for index in (1, 2)
                ],
            ),
        )
    )
    for position, color in ((10, "red"), (20, "blue")):
        render = service.plan_render("v1", position, seed=position)
        output = tmp_path / render["output_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x180:d=0.6:r=24",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(output),
            ],
            check=True,
        )
        service.transition_render(render["render_id"], "queued")
        service.transition_render(render["render_id"], "running")
        service.complete_render(render["render_id"], duration_seconds=probe_duration(output))
        service.approve_render(render["render_id"])

    result = Compiler(database, tmp_path).build("v1", CompilationSettings(width=320, height=180, fps=24))
    output = tmp_path / result["output_path"]
    assert output.exists()
    assert result["state"] == "completed"
    assert 0.5 <= probe_duration(output) <= 0.8
