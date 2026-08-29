from __future__ import annotations

import pytest

from storyboardctl.database import Database
from storyboardctl.errors import Conflict
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


@pytest.fixture
def service(tmp_path) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="renders",
            title="Renders",
            storyboard=StoryboardSpec(
                name="v1",
                title="V1",
                shots=[
                    ShotSpec(
                        key="shot",
                        title="Shot",
                        description="Shot",
                        prompt="Render me",
                        duration_seconds=2,
                        seed=42,
                        render_settings={"steps": 20},
                    )
                ],
            ),
        )
    )
    return service


def test_render_attempts_are_monotonic_and_paths_never_collide(service) -> None:
    first = service.plan_render("v1", 10)
    second = service.plan_render("v1", 10, seed=99)
    assert (first["attempt_number"], second["attempt_number"]) == (1, 2)
    assert first["seed"] == 42
    assert second["seed"] == 99
    assert first["output_path"] != second["output_path"]
    assert first["workflow_path"] != second["workflow_path"]


def test_retry_is_exact_and_rerender_uses_replacement_seed(service) -> None:
    original = service.plan_render("v1", 10, seed=123, settings={"steps": 8})
    retry = service.retry_render(original["render_id"])
    rerender = service.rerender(original["render_id"], seed=456)
    assert retry["seed"] == 123
    assert retry["settings"] == original["settings"]
    assert rerender["seed"] == 456
    assert rerender["settings"] == original["settings"]
    assert retry["source_render_id"] == original["render_id"]


def test_state_machine_refuses_invalid_transitions(service, tmp_path) -> None:
    render = service.plan_render("v1", 10)
    service.transition_render(render["render_id"], "queued", comfy_prompt_id="prompt-1")
    service.transition_render(render["render_id"], "running")
    output = tmp_path / render["output_path"]
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video")
    completed = service.complete_render(render["render_id"], duration_seconds=2.2)
    assert completed["state"] == "completed"
    assert completed["output_sha256"]
    with pytest.raises(Conflict, match="transition"):
        service.transition_render(render["render_id"], "running")

