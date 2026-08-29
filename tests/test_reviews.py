from __future__ import annotations

from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


def test_approval_supersedes_history_carries_to_clone_and_not_revision(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="review",
            title="Review",
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
                    )
                ],
            ),
        )
    )
    first = service.plan_render("v1", 10, seed=1)
    second = service.plan_render("v1", 10, seed=2)
    for render in (first, second):
        output = tmp_path / render["output_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(render["render_id"].encode())
        service.transition_render(render["render_id"], "queued")
        service.transition_render(render["render_id"], "running")
        service.complete_render(render["render_id"], duration_seconds=1.2)

    service.approve_render(first["render_id"], notes="first choice")
    service.approve_render(second["render_id"], notes="better")
    assert service.approved_render("v1", 10)["render_id"] == second["render_id"]
    assert len(service.review_history(second["render_id"])) == 1

    service.clone_storyboard("v1", "v2")
    assert service.approved_render("v2", 10)["render_id"] == second["render_id"]
    service.revise_shot("v2", 10, {"prompt": "Changed"})
    assert service.approved_render("v2", 10) is None
    assert service.approved_render("v1", 10)["render_id"] == second["render_id"]

    service.reject_render(second["render_id"], notes="noticed a flaw")
    assert service.approved_render("v1", 10) is None
    assert [item["decision"] for item in service.review_history(second["render_id"])] == [
        "approved",
        "rejected",
    ]
