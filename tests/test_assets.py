from __future__ import annotations

import pytest

from storyboardctl.database import Database
from storyboardctl.errors import IntegrityFailure
from storyboardctl.models import AssetKind, ProjectSpec, ShotSpec, StoryboardSpec
from storyboardctl.service import StoryboardService


@pytest.fixture
def service(tmp_path) -> StoryboardService:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    service = StoryboardService(database, tmp_path)
    service.import_spec(
        ProjectSpec(
            slug="assets",
            title="Assets",
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
    return service


def test_registers_hashes_and_verifies_assets(service, tmp_path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "frame.png").write_bytes(b"frame-v1")
    registered = service.add_asset("frame", AssetKind.image, "assets/frame.png")
    assert registered["sha256"]
    assert service.verify_asset("frame")["verified"] is True

    (assets / "frame.png").write_bytes(b"replaced")
    with pytest.raises(IntegrityFailure, match="hash mismatch"):
        service.verify_asset("frame")


def test_linking_an_ordered_asset_creates_a_new_shot_revision(service, tmp_path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "frame.png").write_bytes(b"frame")
    service.add_asset("frame", AssetKind.image, "assets/frame.png")
    before = service.list_shots("v1")[0]
    result = service.link_asset("v1", 10, "frame", role="first_frame", order=0)
    after = service.list_shots("v1")[0]
    assert result["revision_number"] == 2
    assert after["revision_id"] != before["revision_id"]

