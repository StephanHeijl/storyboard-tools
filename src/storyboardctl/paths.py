from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from storyboardctl.errors import IntegrityFailure, ValidationFailure


def normalize_relative_path(value: str) -> str:
    normalized_input = value.replace("\\", "/")
    path = PurePosixPath(normalized_input)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() in ("", ".")
        or path.as_posix() != normalized_input
    ):
        raise ValidationFailure(f"path must be a normalized project-relative path: {value!r}")
    return path.as_posix()


def resolve_project_path(
    project_root: str | Path, relative_path: str, *, must_exist: bool = False
) -> Path:
    root = Path(project_root).resolve()
    relative = normalize_relative_path(relative_path)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise IntegrityFailure(f"path resolves outside project root: {relative!r}") from error
    if must_exist and not candidate.is_file():
        raise IntegrityFailure(f"project file does not exist: {relative!r}")
    return candidate


def file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()

