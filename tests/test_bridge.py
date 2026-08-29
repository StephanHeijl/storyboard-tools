from __future__ import annotations

import shutil
import subprocess

import pytest

from storyboardctl.compiler import probe_duration
from storyboardctl.database import Database
from storyboardctl.errors import Conflict, IntegrityFailure
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_bridge_promotes_approved_last_frame_into_one_target_revision(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="bridge",
            title="Bridge",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(key="one", title="One", description="One", prompt="One", duration_seconds=0.3),
                    ShotSpec(key="two", title="Two", description="Two", prompt="Two", duration_seconds=0.3),
                ],
            ),
        )
    )
    render = service.plan_render("v1", 10, seed=1)
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
            "color=c=green:s=320x180:d=0.6:r=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
    )
    service.transition_render(render["render_id"], "queued")
    service.transition_render(render["render_id"], "running")
    service.complete_render(render["render_id"], duration_seconds=probe_duration(output))
    service.approve_render(render["render_id"])

    result = service.bridge_shots("v1", 10, 20, expect_snapshot=0)

    assert result["snapshot"] == 1
    assert result["revision_number"] == 2
    assert result["source_render_id"] == render["render_id"]
    assert (tmp_path / result["asset_path"]).is_file()
    target = service.list_shots("v1")[1]
    assert target["render_mode"] == "image_to_video"
    target_render = service.plan_render("v1", 20, seed=2)
    assert service.render_details(target_render["render_id"])["assets"] == [
        {
            "asset_key": result["asset_key"],
            "kind": "image",
            "path": result["asset_path"],
            "sha256": result["asset_sha256"],
            "role": "first_frame",
            "sort_order": 0,
        }
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_bridge_never_overwrites_an_existing_destination(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="bridge-safe",
            title="Bridge Safe",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(key="one", title="One", description="One", prompt="One", duration_seconds=0.3),
                    ShotSpec(key="two", title="Two", description="Two", prompt="Two", duration_seconds=0.3),
                ],
            ),
        )
    )
    render = service.plan_render("v1", 10, seed=1)
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
            "color=c=green:s=320x180:d=0.6:r=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
    )
    service.transition_render(render["render_id"], "queued")
    service.transition_render(render["render_id"], "running")
    service.complete_render(render["render_id"], duration_seconds=probe_duration(output))
    service.approve_render(render["render_id"])
    fragment = render["render_id"].split("-")[0]
    destination = tmp_path / f"assets/continuity/bridge-0010-0020-{fragment}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"pre-existing")

    with pytest.raises(IntegrityFailure, match="existing continuity"):
        service.bridge_shots("v1", 10, 20)

    assert destination.read_bytes() == b"pre-existing"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_bridge_keeps_published_frame_when_database_commit_loses_a_race(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="bridge-race",
            title="Bridge Race",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(key="one", title="One", description="One", prompt="One", duration_seconds=0.3),
                    ShotSpec(key="two", title="Two", description="Two", prompt="Two", duration_seconds=0.3),
                ],
            ),
        )
    )
    render = service.plan_render("v1", 10, seed=1)
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
            "color=c=green:s=320x180:d=0.6:r=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
    )
    service.transition_render(render["render_id"], "queued")
    service.transition_render(render["render_id"], "running")
    service.complete_render(render["render_id"], duration_seconds=probe_duration(output))
    service.approve_render(render["render_id"])

    real_run = subprocess.run

    def race_after_extract(*args, **kwargs):
        result = real_run(*args, **kwargs)
        with database.transaction(write=True) as connection:
            connection.execute("UPDATE storyboard_versions SET snapshot = 1 WHERE name = 'v1'")
        return result

    monkeypatch.setattr("storyboardctl.service.subprocess.run", race_after_extract)

    with pytest.raises(Conflict, match="snapshot conflict"):
        service.bridge_shots("v1", 10, 20, expect_snapshot=0)

    fragment = render["render_id"].split("-")[0]
    destination = tmp_path / f"assets/continuity/bridge-0010-0020-{fragment}.png"
    assert destination.is_file()
