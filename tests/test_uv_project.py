from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_development_environment_is_uv_managed() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.11"
    assert set(pyproject["dependency-groups"]["dev"]) >= {"pytest>=8.3", "ruff>=0.6", "mypy>=1.11"}
    assert all(not dependency.startswith("build") for dependency in pyproject["dependency-groups"]["dev"])
    assert "dev" not in pyproject["project"].get("optional-dependencies", {})

    assert "uv sync" in readme
    assert "uv run storyboardctl --help" in readme
    assert "python -m venv" not in readme
    assert "pip install" not in readme
    assert "```mermaid" in readme
    assert "STORYBOARDCTL_COMFY_URL" in readme
    assert "STORYBOARDCTL_COMFY_TOKEN" in readme
    assert "uv run storyboardctl comfy ping" in readme
    assert "uv run storyboardctl comfy queue" in readme
    assert "uv run storyboardctl comfy preflight" in readme
    assert "ffmpeg" in readme and "ffprobe" in readme
    assert "uv sync" in contributing
    assert "uv build" in contributing
    assert "python -m build" not in contributing
    assert "--extra dev" not in contributing
    assert "uv sync --locked" in workflow
    assert "uv build" in workflow
