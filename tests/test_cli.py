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

    write_spec(tmp_path)
    invoke(tmp_path, "import", "spec", "spec.json")
    malformed = invoke(tmp_path, "shot", "revise", "v1", "10", "--changes", "{broken")
    assert malformed.exit_code == 2
    assert json.loads(malformed.stderr)["code"] == "validation_error"


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


def test_status_audit_and_render_list_commands_emit_json(tmp_path) -> None:
    write_spec(tmp_path)
    invoke(tmp_path, "init")
    invoke(tmp_path, "import", "spec", "spec.json")
    invoke(tmp_path, "render", "shot", "v1", "10", "--plan-only")

    renders = invoke(tmp_path, "render", "list", "--version", "v1", "--position", "10")
    status = invoke(tmp_path, "production", "status", "--version", "v1")
    audit = invoke(tmp_path, "storyboard", "audit", "v1")

    assert json.loads(renders.stdout)[0]["position"] == 10
    assert json.loads(status.stdout)["versions"][0]["shots"] == 1
    assert json.loads(audit.stdout)["issues"][0]["code"] == "missing_approved_render"


def test_comfy_diagnostic_commands_and_async_render_execution(tmp_path, monkeypatch) -> None:
    write_spec(tmp_path)
    invoke(tmp_path, "init")
    invoke(tmp_path, "import", "spec", "spec.json")
    planned = json.loads(invoke(tmp_path, "render", "shot", "v1", "10", "--plan-only").stdout)

    monkeypatch.setattr("storyboardctl.cli.ComfyClient.ping", lambda _self: {"ok": True, "comfyui_version": "test"})
    monkeypatch.setattr(
        "storyboardctl.cli.ComfyClient.queue_status",
        lambda _self: {"running": 0, "pending": 0, "queue_running": [], "queue_pending": []},
    )
    monkeypatch.setattr(
        "storyboardctl.cli.ComfyClient.preflight",
        lambda _self, **_kwargs: {"ok": True, "missing_nodes": [], "missing_models": []},
    )

    class FakeRunner:
        def submit(self, render_id: str):
            return {"render_id": render_id, "state": "queued"}

        def execute(self, render_id: str, **_kwargs):
            return {"render_id": render_id, "state": "completed"}

        def wait(self, render_id: str, **_kwargs):
            return {"render_id": render_id, "state": "completed"}

    monkeypatch.setattr("storyboardctl.cli._render_runner", lambda _service: FakeRunner())

    assert json.loads(invoke(tmp_path, "comfy", "ping").stdout)["ok"] is True
    assert json.loads(invoke(tmp_path, "comfy", "queue").stdout)["running"] == 0
    assert json.loads(invoke(tmp_path, "comfy", "preflight").stdout)["ok"] is True
    submitted = invoke(tmp_path, "render", "execute", planned["render_id"], "--no-wait")
    assert json.loads(submitted.stdout)["state"] == "queued"
    waited = invoke(tmp_path, "render", "wait", planned["render_id"])
    assert json.loads(waited.stdout)["state"] == "completed"
