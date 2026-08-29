from __future__ import annotations

from storyboardctl.compiler import Compiler
from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.paths import file_sha256
from storyboardctl.service import StoryboardService


def completed_compilation(service: StoryboardService, tmp_path) -> dict:
    manifest = Compiler(service.database, tmp_path).create_manifest("v1")
    output = tmp_path / manifest["output_path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(manifest["compilation_id"].encode())
    with service.database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE compilations SET state = 'completed', output_sha256 = ?, completed_at = 'now' WHERE id = ?",
            (file_sha256(output), manifest["compilation_id"]),
        )
    return manifest


def test_compilation_reviews_select_supersede_reject_and_preserve_history(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="cut-review",
            title="Cut Review",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[ShotSpec(key="one", title="One", description="One", prompt="One", duration_seconds=1)],
            ),
        )
    )
    render = service.plan_render("v1", 10)
    render_output = tmp_path / render["output_path"]
    render_output.parent.mkdir(parents=True, exist_ok=True)
    render_output.write_bytes(b"render")
    service.transition_render(render["render_id"], "queued")
    service.transition_render(render["render_id"], "running")
    service.complete_render(render["render_id"], duration_seconds=1)
    service.approve_render(render["render_id"])
    first = completed_compilation(service, tmp_path)
    second = completed_compilation(service, tmp_path)

    service.approve_compilation(first["compilation_id"], reviewer="agent", notes="first")
    service.approve_compilation(second["compilation_id"], reviewer="agent", notes="better")
    assert service.approved_compilation("v1")["compilation_id"] == second["compilation_id"]

    service.reject_compilation(second["compilation_id"], notes="cut issue")
    assert service.approved_compilation("v1") is None
    assert [item["decision"] for item in service.compilation_review_history(second["compilation_id"])] == [
        "approved",
        "rejected",
    ]
