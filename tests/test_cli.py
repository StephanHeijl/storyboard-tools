from __future__ import annotations

import json

from typer.testing import CliRunner

from storyboardctl.cli import app
from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec

runner = CliRunner()


def write_spec(tmp_path) -> None:
    spec = ProjectSpec(
        slug="cli",
        title="CLI",
        storyboard=StoryboardSpec(
            name="v1",
            title="V1",
            shots=[
                ShotSpec(
                    key="one",
                    title="One",
                    description="One",
                    prompt="One",
                    duration_seconds=1,
                )
            ],
        ),
    )
    (tmp_path / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")


def invoke(tmp_path, *args: str):
    return runner.invoke(
        app,
        ["--root", str(tmp_path), "--db", "storyboard.db", *args],
        catch_exceptions=False,
    )


def test_schema_init_import_and_list_emit_json(tmp_path) -> None:
    write_spec(tmp_path)
    schema = invoke(tmp_path, "schema")
    assert schema.exit_code == 0
    assert json.loads(schema.stdout)["title"] == "ProjectSpec"

    initialized = invoke(tmp_path, "init")
    assert json.loads(initialized.stdout)["initialized"] is True
    imported = invoke(tmp_path, "import", "spec", "spec.json")
    assert json.loads(imported.stdout)["version"] == "v1"
    shots = invoke(tmp_path, "shot", "list", "v1")
    assert json.loads(shots.stdout)[0]["position"] == 10


def test_agent_commands_add_revise_clone_remove_and_plan_render(tmp_path) -> None:
    write_spec(tmp_path)
    invoke(tmp_path, "init")
    invoke(tmp_path, "import", "spec", "spec.json")
    second = ShotSpec(
        key="two",
        title="Two",
        description="Two",
        prompt="Two",
        duration_seconds=1,
    )
    (tmp_path / "shot.json").write_text(second.model_dump_json(), encoding="utf-8")

    added = invoke(tmp_path, "shot", "add", "v1", "shot.json", "--after", "10")
    assert json.loads(added.stdout)["position"] == 20
    revised = invoke(tmp_path, "shot", "revise", "v1", "20", "--changes", '{"prompt":"Changed"}')
    assert json.loads(revised.stdout)["revision_number"] == 2
    cloned = invoke(tmp_path, "storyboard", "clone", "v1", "short")
    assert json.loads(cloned.stdout)["version"] == "short"
    removed = invoke(tmp_path, "shot", "remove", "short", "20")
    assert json.loads(removed.stdout)["snapshot"] == 1
    planned = invoke(tmp_path, "render", "shot", "v1", "10", "--plan-only")
    assert json.loads(planned.stdout)["state"] == "planned"


def test_expected_errors_are_json_on_stderr_with_stable_exit_code(tmp_path) -> None:
    invoke(tmp_path, "init")
    result = invoke(tmp_path, "shot", "list", "missing")
    assert result.exit_code == 3
    error = json.loads(result.stderr)
    assert error["code"] == "not_found"
    assert result.stdout == ""


def test_table_format_is_available_for_interactive_use(tmp_path) -> None:
    write_spec(tmp_path)
    invoke(tmp_path, "init")
    invoke(tmp_path, "import", "spec", "spec.json")
    result = runner.invoke(
        app,
        ["--root", str(tmp_path), "--format", "table", "storyboard", "list"],
        catch_exceptions=False,
    )
    assert "name" in result.stdout
    assert "v1" in result.stdout
