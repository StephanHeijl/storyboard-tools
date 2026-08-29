from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from storyboardctl.compiler import CompilationSettings, Compiler, probe_duration
from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.quality import QualityInspector
from storyboardctl.service import StoryboardService


def make_clip(path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "sine=frequency=440:sample_rate=48000:duration=0.6",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def prepared_media(tmp_path):
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="qc",
            title="QC",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(key="red", title="Red", description="Red", prompt="Red", duration_seconds=0.3),
                    ShotSpec(key="blue", title="Blue", description="Blue", prompt="Blue", duration_seconds=0.3),
                ],
            ),
        )
    )
    renders = []
    for position, color in ((10, "red"), (20, "blue")):
        render = service.plan_render("v1", position, seed=position)
        output = tmp_path / render["output_path"]
        make_clip(output, color)
        service.transition_render(render["render_id"], "queued")
        service.transition_render(render["render_id"], "running")
        service.complete_render(render["render_id"], duration_seconds=probe_duration(output))
        service.approve_render(render["render_id"])
        renders.append(render)
    compilation = Compiler(database, tmp_path).build("v1", CompilationSettings(width=320, height=180, fps=24))
    return database, renders, compilation


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_render_qc_generates_and_persists_agent_readable_report(tmp_path) -> None:
    database, renders, _compilation = prepared_media(tmp_path)

    report = QualityInspector(database, tmp_path).inspect_render(renders[0]["render_id"])

    assert report["entity_type"] == "render"
    assert report["decode_ok"] is True
    assert report["duration_seconds"] >= 0.5
    assert report["audio"]["present"] is True
    assert (tmp_path / report["contact_sheet_path"]).is_file()
    assert json.loads((tmp_path / report["report_path"]).read_text(encoding="utf-8"))["sha256"] == report["sha256"]
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quality_reports WHERE render_id = ?", (renders[0]["render_id"],)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_compilation_qc_generates_boundary_frames(tmp_path) -> None:
    database, _renders, compilation = prepared_media(tmp_path)

    report = QualityInspector(database, tmp_path).inspect_compilation(compilation["compilation_id"])

    assert report["entity_type"] == "compilation"
    assert report["decode_ok"] is True
    assert len(report["boundary_sheet_paths"]) == 1
    assert (tmp_path / report["boundary_sheet_paths"][0]).is_file()
