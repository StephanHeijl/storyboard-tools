from __future__ import annotations

import pytest
from pydantic import ValidationError

from storyboardctl.models import (
    AssetKind,
    AssetRole,
    AssetSpec,
    DialogueCueSpec,
    MusicCueSpec,
    MusicRelationship,
    ProjectSpec,
    RenderMode,
    ShotAssetSpec,
    ShotLinkKind,
    ShotLinkSpec,
    ShotSpec,
    StoryboardSpec,
)


def example_project() -> ProjectSpec:
    return ProjectSpec(
        slug="moonlight-delivery",
        title="Moonlight Delivery",
        assets=[
            AssetSpec(key="courier", kind=AssetKind.image, path="assets/courier.png"),
            AssetSpec(key="theme", kind=AssetKind.audio, path="assets/theme.wav"),
        ],
        music=[MusicCueSpec(key="theme", asset_key="theme", title="Moon Theme")],
        storyboard=StoryboardSpec(
            name="v1",
            title="First Cut",
            shots=[
                ShotSpec(
                    key="arrival",
                    position=10,
                    title="Courier arrives",
                    description="A courier lands on the moon.",
                    prompt="Wide shot of a courier landing softly.",
                    duration_seconds=4.0,
                    render_mode=RenderMode.image_to_video,
                    assets=[
                        ShotAssetSpec(asset_key="courier", role=AssetRole.reference, order=0),
                        ShotAssetSpec(asset_key="courier", role=AssetRole.first_frame, order=1),
                    ],
                    music={"theme": MusicRelationship.starts_here},
                ),
                ShotSpec(
                    key="handoff",
                    title="Parcel handoff",
                    description="The parcel changes hands.",
                    prompt="Close shot of the parcel changing hands.",
                    duration_seconds=3.5,
                    links=[ShotLinkSpec(target_shot_key="arrival", kind=ShotLinkKind.continuity)],
                ),
            ],
        ),
    )


def test_project_schema_round_trips_strict_json() -> None:
    project = example_project()
    restored = ProjectSpec.model_validate_json(project.model_dump_json())
    assert restored == project
    assert restored.storyboard.shots[1].position is None
    assert restored.storyboard.shots[0].assets[1].role is AssetRole.first_frame


def test_dialogue_cues_round_trip_and_must_fit_without_overlap() -> None:
    shot = ShotSpec(
        key="dialogue",
        title="Dialogue",
        description="Two people speak.",
        prompt="Medium two-shot.",
        duration_seconds=4,
        dialogue=[
            DialogueCueSpec(
                speaker="Alice",
                speaker_id="S1",
                text="We need to leave.",
                language="English",
                start_seconds=0.5,
                end_seconds=1.8,
            ),
            DialogueCueSpec(
                speaker="Bob",
                speaker_id="S2",
                text="I know.",
                language="English",
                start_seconds=2,
                end_seconds=3,
            ),
        ],
    )
    assert ShotSpec.model_validate_json(shot.model_dump_json()) == shot

    payload = shot.model_dump(mode="json")
    payload["dialogue"][1]["start_seconds"] = 1.5
    with pytest.raises(ValidationError, match="overlap"):
        ShotSpec.model_validate(payload)

    payload = shot.model_dump(mode="json")
    payload["dialogue"][1]["end_seconds"] = 5
    with pytest.raises(ValidationError, match="duration"):
        ShotSpec.model_validate(payload)


def test_dialogue_cue_rejects_invalid_speaker_id_and_empty_text() -> None:
    with pytest.raises(ValidationError, match="speaker_id"):
        DialogueCueSpec(
            speaker="Alice",
            speaker_id="Alice",
            text="Hello",
            start_seconds=0,
            end_seconds=1,
        )
    with pytest.raises(ValidationError):
        DialogueCueSpec(
            speaker="Alice",
            speaker_id="S1",
            text="",
            start_seconds=0,
            end_seconds=1,
        )


def test_models_reject_unknown_fields_and_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ShotSpec(
            key="bad",
            title="Bad",
            description="Bad shot",
            prompt="Bad prompt",
            duration_seconds=0,
            surprise=True,
        )
    with pytest.raises(ValidationError):
        ShotSpec(
            key="bad",
            position=-10,
            title="Bad",
            description="Bad shot",
            prompt="Bad prompt",
            duration_seconds=1,
        )


def test_project_rejects_duplicate_keys_and_broken_references() -> None:
    project = example_project().model_dump(mode="json")
    project["assets"].append(project["assets"][0])
    with pytest.raises(ValidationError, match="duplicate asset key"):
        ProjectSpec.model_validate(project)

    project = example_project().model_dump(mode="json")
    project["storyboard"]["shots"][0]["assets"][0]["asset_key"] = "missing"
    with pytest.raises(ValidationError, match="unknown asset"):
        ProjectSpec.model_validate(project)


def test_json_schema_is_suitable_for_llm_structured_output() -> None:
    schema = ProjectSpec.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "StoryboardSpec" in schema["$defs"]
    shot_schema = schema["$defs"]["ShotSpec"]
    assert shot_schema["additionalProperties"] is False
    assert "duration_seconds" in shot_schema["required"]


def test_asset_paths_must_be_project_relative() -> None:
    for unsafe in ("/tmp/image.png", "../image.png", "assets/../../image.png"):
        with pytest.raises(ValidationError, match="project-relative"):
            AssetSpec(key="unsafe", kind=AssetKind.image, path=unsafe)


def test_unknown_interchange_schema_version_is_rejected() -> None:
    payload = example_project().model_dump(mode="json")
    payload["schema_version"] = 2
    with pytest.raises(ValidationError, match="schema_version"):
        ProjectSpec.model_validate(payload)
