from __future__ import annotations

from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


def prepared_service(tmp_path) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="status-demo",
            title="Status Demo",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(key="one", title="One", description="One", prompt="One", duration_seconds=1),
                    ShotSpec(key="two", title="Two", description="Two", prompt="Two", duration_seconds=1),
                ],
            ),
        )
    )
    render = service.plan_render("v1", 10, seed=10)
    output = tmp_path / render["output_path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"video")
    service.transition_render(render["render_id"], "queued")
    service.transition_render(render["render_id"], "running")
    service.complete_render(render["render_id"], duration_seconds=1)
    service.approve_render(render["render_id"])
    failed = service.plan_render("v1", 20, seed=20)
    service.transition_render(failed["render_id"], "failed", error_message="test failure")
    return service


def test_render_list_filters_by_version_position_and_state(tmp_path) -> None:
    service = prepared_service(tmp_path)

    failed = service.list_renders(version_name="v1", position=20, state="failed")

    assert len(failed) == 1
    assert failed[0]["position"] == 20
    assert failed[0]["shot_key"] == "two"
    assert failed[0]["attempt_number"] == 1
    assert failed[0]["state"] == "failed"
    assert failed[0]["approved"] is False


def test_production_status_and_storyboard_audit_explain_readiness(tmp_path) -> None:
    service = prepared_service(tmp_path)

    status = service.production_status(version_name="v1")
    audit = service.audit_storyboard("v1")

    assert status["production"] == {"slug": "status-demo", "title": "Status Demo"}
    assert status["versions"] == [
        {
            "version": "v1",
            "snapshot": 0,
            "status": "draft",
            "shots": 2,
            "approved_shots": 1,
            "unapproved_shots": 1,
            "render_states": {"completed": 1, "failed": 1},
            "compilations": 0,
            "ready_to_compile": False,
            "issues": [
                {
                    "code": "missing_approved_render",
                    "position": 20,
                    "shot_key": "two",
                    "latest_render_state": "failed",
                }
            ],
            "latest_compilation": None,
        }
    ]
    assert audit["ready_to_compile"] is False
    assert audit["issues"] == [
        {
            "code": "missing_approved_render",
            "position": 20,
            "shot_key": "two",
            "latest_render_state": "failed",
        }
    ]


def test_audit_rejects_stale_approved_output(tmp_path) -> None:
    service = prepared_service(tmp_path)
    approved = service.approved_render("v1", 10)
    assert approved is not None
    (tmp_path / approved["output_path"]).unlink()

    audit = service.audit_storyboard("v1")
    status = service.production_status(version_name="v1")

    assert audit["ready_to_compile"] is False
    assert audit["issues"][0]["code"] == "approved_output_missing"
    assert audit["issues"][0]["position"] == 10
    assert status["versions"][0]["ready_to_compile"] is False


def test_render_discovery_keeps_attempts_from_replaced_revisions(tmp_path) -> None:
    service = prepared_service(tmp_path)
    original = service.list_renders(version_name="v1", position=10)[0]
    service.revise_shot("v1", 10, {"prompt": "A new revision"})

    renders = service.list_renders(version_name="v1", position=10)

    assert any(item["render_id"] == original["render_id"] for item in renders)
    assert any(item["revision_selected"] is False for item in renders)
