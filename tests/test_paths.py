from __future__ import annotations

import hashlib

import pytest

from storyboardctl.errors import IntegrityFailure, ValidationFailure
from storyboardctl.paths import file_sha256, normalize_relative_path, resolve_project_path


def test_normalizes_safe_project_relative_paths() -> None:
    assert normalize_relative_path("assets/courier.png") == "assets/courier.png"
    assert normalize_relative_path("assets\\courier.png") == "assets/courier.png"
    for value in ("", ".", "/tmp/file", "../file", "assets/../../file"):
        with pytest.raises(ValidationFailure):
            normalize_relative_path(value)


def test_resolve_refuses_symlink_escape(tmp_path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (project / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntegrityFailure, match="outside project root"):
        resolve_project_path(project, "escape/secret.txt", must_exist=True)


def test_hashing_and_missing_file_validation(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "asset.bin"
    target.write_bytes(b"storyboard")
    assert file_sha256(target) == hashlib.sha256(b"storyboard").hexdigest()
    assert resolve_project_path(project, "asset.bin", must_exist=True) == target.resolve()
    with pytest.raises(IntegrityFailure, match="does not exist"):
        resolve_project_path(project, "missing.bin", must_exist=True)
