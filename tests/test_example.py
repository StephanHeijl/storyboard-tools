from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from storyboardctl.database import Database
from storyboardctl.models import ProjectSpec
from storyboardctl.service import StoryboardService

ROOT = Path(__file__).resolve().parents[1]


def test_python_example_matches_json_and_imports(tmp_path) -> None:
    module_path = ROOT / "examples" / "moonlight_delivery.py"
    spec = importlib.util.spec_from_file_location("moonlight_delivery", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = module.build_project()
    committed = json.loads((ROOT / "examples" / "moonlight_delivery.json").read_text(encoding="utf-8"))
    assert project.model_dump(mode="json") == committed

    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    result = StoryboardService(database, tmp_path).import_spec(ProjectSpec.model_validate(committed))
    assert result["shots"] == 3
    assert result["version"] == "v1"
