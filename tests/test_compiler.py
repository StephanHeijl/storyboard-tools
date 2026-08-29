from __future__ import annotations

import json

import pytest

from storyboardctl.compiler import Compiler, CompilationSettings
from storyboardctl.database import Database
from storyboardctl.errors import Conflict, IntegrityFailure
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


def prepared_service(tmp_path, *, approve_second: bool = True) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="compile",
            title="Compile",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="one",
                        title="One",
                        description="One",
                        prompt="One",
                        duration_seconds=1,
                    ),
                    ShotSpec(
                        key="two",
                        title="Two",
                        description="Two",
                        prompt="Two",
                        duration_seconds=1.5,
                    ),
                ],
            ),
        )
    )
    for position in (10, 20):
        render = service.plan_render("v1", position, seed=position)
        output = tmp_path / render["output_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"video-{position}".encode())
        service.transition_render(render["render_id"], "queued")
        service.transition_render(render["render_id"], "running")
        service.complete_render(render["render_id"], duration_seconds=2)
        if position == 10 or approve_second:
            service.approve_render(render["render_id"])
    return service


def test_manifest_selects_approved_renders_in_order_and_numbers_snapshots(tmp_path) -> None:
    service = prepared_service(tmp_path)
    compiler = Compiler(service.database, tmp_path)
    first = compiler.create_manifest("v1", CompilationSettings(width=736, height=416))
    second = compiler.create_manifest("v1", CompilationSettings(width=736, height=416))

    assert [item["position"] for item in first["items"]] == [10, 20]
    assert first["compilation_number"] == 1
    assert second["compilation_number"] == 2
    assert first["manifest_path"] != second["manifest_path"]
    on_disk = json.loads((tmp_path / first["manifest_path"]).read_text(encoding="utf-8"))
    assert on_disk == first
    assert all(item["source_sha256"] for item in first["items"])


def test_manifest_fails_before_writing_when_approval_is_missing_or_stale(tmp_path) -> None:
    service = prepared_service(tmp_path, approve_second=False)
    compiler = Compiler(service.database, tmp_path)
    with pytest.raises(Conflict, match="approved render.*20"):
        compiler.create_manifest("v1")

    service = prepared_service(tmp_path / "stale")
    render = service.approved_render("v1", 10)
    assert render is not None
    (tmp_path / "stale" / render["output_path"]).write_bytes(b"replaced")
    with pytest.raises(IntegrityFailure, match="hash mismatch"):
        Compiler(service.database, tmp_path / "stale").create_manifest("v1")


def test_manifest_rejects_render_shorter_than_intended_shot(tmp_path) -> None:
    service = prepared_service(tmp_path)
    with service.database.transaction(write=True) as connection:
        connection.execute("UPDATE renders SET duration_seconds = 0.5 WHERE attempt_number = 1")
    with pytest.raises(IntegrityFailure, match="shorter"):
        Compiler(service.database, tmp_path).create_manifest("v1")

