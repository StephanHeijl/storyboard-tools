from __future__ import annotations

import pytest

from storyboardctl.database import Database
from storyboardctl.errors import Conflict
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


def project_spec() -> ProjectSpec:
    return ProjectSpec(
        slug="sample",
        title="Sample",
        storyboard=StoryboardSpec(
            name="v1",
            title="Version One",
            shots=[
                ShotSpec(
                    key="one",
                    title="One",
                    description="First",
                    prompt="First prompt",
                    duration_seconds=2,
                ),
                ShotSpec(
                    key="two",
                    title="Two",
                    description="Second",
                    prompt="Second prompt",
                    duration_seconds=3,
                ),
            ],
        ),
    )


@pytest.fixture
def service(tmp_path) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    result = StoryboardService(database, tmp_path)
    result.import_spec(project_spec())
    return result


def test_import_numbers_shots_by_ten_and_clone_shares_revisions(service) -> None:
    original = service.list_shots("v1")
    assert [shot["position"] for shot in original] == [10, 20]

    clone = service.clone_storyboard("v1", "short", title="Short Cut")
    copied = service.list_shots("short")
    assert clone["snapshot"] == 0
    assert [shot["revision_id"] for shot in copied] == [shot["revision_id"] for shot in original]


def test_revising_a_shot_is_immutable_and_only_changes_target_version(service) -> None:
    service.clone_storyboard("v1", "v2")
    before_v1 = service.list_shots("v1")[0]
    result = service.revise_shot("v2", 10, {"prompt": "A changed prompt"}, expect_snapshot=0)
    after_v1 = service.list_shots("v1")[0]
    after_v2 = service.list_shots("v2")[0]

    assert result["snapshot"] == 1
    assert after_v1["revision_id"] == before_v1["revision_id"]
    assert after_v2["revision_id"] != before_v1["revision_id"]
    assert after_v2["revision_number"] == 2
    assert after_v2["prompt"] == "A changed prompt"


def test_insert_uses_midpoints_and_reports_exhausted_gap(service) -> None:
    inserted = service.add_shot(
        "v1",
        ShotSpec(
            key="middle",
            title="Middle",
            description="Middle",
            prompt="Middle",
            duration_seconds=1,
        ),
        after_position=10,
    )
    assert inserted["position"] == 15

    service.add_shot(
        "v1",
        ShotSpec(
            key="near",
            position=11,
            title="Near",
            description="Near",
            prompt="Near",
            duration_seconds=1,
        ),
    )
    with pytest.raises(Conflict, match="no integer position"):
        service.add_shot(
            "v1",
            ShotSpec(
                key="impossible",
                title="Impossible",
                description="Impossible",
                prompt="Impossible",
                duration_seconds=1,
            ),
            after_position=10,
        )


def test_snapshot_conflict_lock_remove_and_renumber(service) -> None:
    service.remove_shot("v1", 20, expect_snapshot=0)
    assert [shot["position"] for shot in service.list_shots("v1")] == [10]
    with pytest.raises(Conflict, match="snapshot"):
        service.renumber_storyboard("v1", expect_snapshot=0)

    service.add_shot(
        "v1",
        ShotSpec(
            key="replacement",
            position=99,
            title="Replacement",
            description="Replacement",
            prompt="Replacement",
            duration_seconds=1,
        ),
    )
    service.renumber_storyboard("v1", step=10)
    assert [shot["position"] for shot in service.list_shots("v1")] == [10, 20]

    service.lock_storyboard("v1")
    with pytest.raises(Conflict, match="locked"):
        service.remove_shot("v1", 10)
