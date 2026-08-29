from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetKind(StrEnum):
    image = "image"
    audio = "audio"
    video = "video"
    document = "document"
    other = "other"


class AssetRole(StrEnum):
    reference = "reference"
    first_frame = "first_frame"
    last_frame = "last_frame"
    attachment = "attachment"
    source_audio = "source_audio"


class RenderMode(StrEnum):
    text_to_video = "text_to_video"
    image_to_video = "image_to_video"
    reference_to_video = "reference_to_video"
    custom = "custom"


class ShotLinkKind(StrEnum):
    continuity = "continuity"
    derives_first_frame = "derives_first_frame"
    derives_last_frame = "derives_last_frame"
    related = "related"


class MusicRelationship(StrEnum):
    starts_here = "starts_here"
    continues = "continues"
    associated = "associated"


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() in ("", "."):
        raise ValueError("path must be a normalized project-relative path")
    normalized = path.as_posix()
    if normalized != value.replace("\\", "/"):
        raise ValueError("path must be a normalized project-relative path")
    return normalized


class AssetSpec(StrictModel):
    key: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: AssetKind
    path: str
    title: str | None = None
    media_type: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        self.path = _validate_relative_path(self.path)
        return self


class ShotAssetSpec(StrictModel):
    asset_key: str = Field(min_length=1)
    role: AssetRole = AssetRole.reference
    order: int = Field(default=0, ge=0)
    notes: str | None = None


class ShotLinkSpec(StrictModel):
    target_shot_key: str = Field(min_length=1)
    kind: ShotLinkKind = ShotLinkKind.related
    notes: str | None = None


class MusicCueSpec(StrictModel):
    key: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    asset_key: str = Field(min_length=1)
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShotSpec(StrictModel):
    key: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    position: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    duration_seconds: float = Field(gt=0)
    render_mode: RenderMode = RenderMode.text_to_video
    seed: int | None = Field(default=None, ge=0)
    adapter: str = "h3"
    render_settings: dict[str, Any] = Field(default_factory=dict)
    assets: list[ShotAssetSpec] = Field(default_factory=list)
    links: list[ShotLinkSpec] = Field(default_factory=list)
    music: dict[str, MusicRelationship] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_asset_order(self) -> Self:
        identities = [(item.role, item.order) for item in self.assets]
        if len(identities) != len(set(identities)):
            raise ValueError("shot asset role/order pairs must be unique")
        return self


class StoryboardSpec(StrictModel):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    title: str
    shots: list[ShotSpec]
    description: str | None = None

    @model_validator(mode="after")
    def validate_shots(self) -> Self:
        keys = [shot.key for shot in self.shots]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate shot key")
        positions = [shot.position for shot in self.shots if shot.position is not None]
        if len(positions) != len(set(positions)):
            raise ValueError("duplicate shot position")
        key_set = set(keys)
        for shot in self.shots:
            for link in shot.links:
                if link.target_shot_key not in key_set:
                    raise ValueError(f"shot {shot.key!r} links to unknown shot {link.target_shot_key!r}")
        return self


class ProjectSpec(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    storyboard: StoryboardSpec
    assets: list[AssetSpec] = Field(default_factory=list)
    music: list[MusicCueSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        asset_keys = [asset.key for asset in self.assets]
        if len(asset_keys) != len(set(asset_keys)):
            raise ValueError("duplicate asset key")
        music_keys = [cue.key for cue in self.music]
        if len(music_keys) != len(set(music_keys)):
            raise ValueError("duplicate music key")
        asset_set = set(asset_keys)
        music_set = set(music_keys)
        for cue in self.music:
            if cue.asset_key not in asset_set:
                raise ValueError(f"music cue {cue.key!r} references unknown asset {cue.asset_key!r}")
        for shot in self.storyboard.shots:
            for linked in shot.assets:
                if linked.asset_key not in asset_set:
                    raise ValueError(f"shot {shot.key!r} references unknown asset {linked.asset_key!r}")
            for music_key in shot.music:
                if music_key not in music_set:
                    raise ValueError(f"shot {shot.key!r} references unknown music {music_key!r}")
        return self
