"""Small fictional storyboard authored with the public Python schema."""

from __future__ import annotations

from storyboardctl.models import (
    AssetKind,
    AssetRole,
    AssetSpec,
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


def build_project() -> ProjectSpec:
    return ProjectSpec(
        slug="moonlight-delivery",
        title="Moonlight Delivery",
        assets=[
            AssetSpec(key="courier", kind=AssetKind.image, path="assets/courier.png"),
            AssetSpec(key="moon", kind=AssetKind.image, path="assets/moon.png"),
            AssetSpec(key="theme", kind=AssetKind.audio, path="assets/theme.wav"),
        ],
        music=[MusicCueSpec(key="theme", asset_key="theme", title="Quiet Orbit")],
        storyboard=StoryboardSpec(
            name="v1",
            title="First Delivery",
            description="A three-shot example with references, continuity, and music metadata.",
            shots=[
                ShotSpec(
                    key="approach",
                    title="Approach",
                    description="A tiny delivery craft approaches the moon.",
                    prompt="Wide view of a tiny delivery craft approaching a quiet moon.",
                    duration_seconds=3,
                    music={"theme": MusicRelationship.starts_here},
                ),
                ShotSpec(
                    key="landing",
                    title="Soft landing",
                    description="The courier lands beside a silver mailbox.",
                    prompt="A lunar courier lands gently beside a silver mailbox.",
                    duration_seconds=4,
                    render_mode=RenderMode.image_to_video,
                    assets=[
                        ShotAssetSpec(asset_key="moon", role=AssetRole.first_frame, order=0),
                        ShotAssetSpec(asset_key="courier", role=AssetRole.reference, order=0),
                    ],
                    links=[ShotLinkSpec(target_shot_key="approach", kind=ShotLinkKind.continuity)],
                    music={"theme": MusicRelationship.continues},
                ),
                ShotSpec(
                    key="delivery",
                    title="Delivery",
                    description="The courier places a glowing parcel in the mailbox.",
                    prompt="Close view of the courier placing a glowing parcel in the mailbox.",
                    duration_seconds=3,
                    render_mode=RenderMode.reference_to_video,
                    assets=[ShotAssetSpec(asset_key="courier", role=AssetRole.reference, order=0)],
                    links=[ShotLinkSpec(target_shot_key="landing", kind=ShotLinkKind.continuity)],
                    music={"theme": MusicRelationship.associated},
                ),
            ],
        ),
    )


if __name__ == "__main__":
    print(build_project().model_dump_json(indent=2))
