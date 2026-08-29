from __future__ import annotations

import sqlite3

import pytest

from storyboardctl.database import Database
from storyboardctl.errors import Conflict, IntegrityFailure
from storyboardctl.models import AssetKind, AssetSpec, ProjectSpec, ShotSpec, StoryboardSpec
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
    with pytest.raises(sqlite3.IntegrityError):
        service.import_spec(spec)

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM production").fetchone()[0] == 0


def test_import_hashes_assets_that_already_exist(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "frame.png").write_bytes(b"authoritative-frame")
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    spec = ProjectSpec(
        slug="hashed",
        title="Hashed",
        assets=[AssetSpec(key="frame", kind=AssetKind.image, path="assets/frame.png")],
        storyboard=StoryboardSpec(name="v1", title="V1", shots=[]),
    )
    service.import_spec(spec)
    with database.connect() as connection:
        digest = connection.execute("SELECT sha256 FROM assets WHERE asset_key = 'frame'").fetchone()[0]
    assert digest is not None
    assert service.verify_asset("frame")["sha256"] == digest


def test_first_verification_pins_an_asset_created_after_import(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    spec = ProjectSpec(
        slug="planned",
        title="Planned",
        assets=[AssetSpec(key="frame", kind=AssetKind.image, path="assets/frame.png")],
        storyboard=StoryboardSpec(name="v1", title="V1", shots=[]),
    )
    service.import_spec(spec)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "frame.png").write_bytes(b"first-version")
    pinned = service.verify_asset("frame")
    assert pinned["verified"] is True

    (tmp_path / "assets" / "frame.png").write_bytes(b"replacement")
    with pytest.raises(IntegrityFailure, match="hash mismatch"):
        service.verify_asset("frame")
