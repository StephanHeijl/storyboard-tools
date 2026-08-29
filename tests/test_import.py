from __future__ import annotations

import pytest

from storyboardctl.database import Database
from storyboardctl.errors import Conflict
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


def test_spec_import_is_atomic_and_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    spec = ProjectSpec(
        slug="atomic",
        title="Atomic",
        storyboard=StoryboardSpec(
            name="v1",
            title="V1",
            shots=[
                ShotSpec(
                    key="only",
                    title="Only",
                    description="Only",
                    prompt="Only",
                    duration_seconds=1,
                )
            ],
        ),
    )

    first = service.import_spec(spec, idempotency_key="import-1")
    second = service.import_spec(spec, idempotency_key="import-1")
    assert second == first
    assert len(service.list_versions()) == 1

    with pytest.raises(Conflict):
        service.import_spec(spec, idempotency_key="different-request")


def test_failed_import_leaves_no_partial_production(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    spec = ProjectSpec(
        slug="atomic",
        title="Atomic",
        storyboard=StoryboardSpec(
            name="v1",
            title="V1",
            shots=[
                ShotSpec(
                    key="only",
                    position=10,
                    title="Only",
                    description="Only",
                    prompt="Only",
                    duration_seconds=1,
                ),
                ShotSpec(
                    key="collision",
                    position=20,
                    title="Collision",
                    description="Collision",
                    prompt="Collision",
                    duration_seconds=1,
                ),
            ],
        ),
    )
    # Force a late database conflict that bypasses the validated input model.
    object.__setattr__(spec.storyboard.shots[1], "position", 10)
    with pytest.raises(Exception):
        service.import_spec(spec)

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM production").fetchone()[0] == 0
