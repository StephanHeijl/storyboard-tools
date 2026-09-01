from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from storyboardctl.boarding import BoardRunner
from storyboardctl.boarding import preflight as board_preflight_check
from storyboardctl.comfy.client import ComfyClient, ComfySettings
from storyboardctl.comfy.h3 import H3Adapter
from storyboardctl.compiler import CompilationSettings, Compiler
from storyboardctl.database import Database
from storyboardctl.errors import StoryboardError, ValidationFailure
from storyboardctl.models import AssetKind, ProjectSpec, ShotSpec
from storyboardctl.output import json_text, table_text
from storyboardctl.preview import PreviewBuilder, PreviewSettings, ffmpeg_has_subtitles
from storyboardctl.quality import QualityInspector
from storyboardctl.rendering import RenderRunner
from storyboardctl.service import StoryboardService

app = typer.Typer(
    name="storyboardctl",
    help="Atomic, agent-facing storyboard production tools.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
import_app = typer.Typer(help="Import structured production data.")
storyboard_app = typer.Typer(help="Manage storyboard versions.")
shot_app = typer.Typer(help="Manage versioned shots.")
asset_app = typer.Typer(help="Manage project media assets.")
render_app = typer.Typer(help="Plan and execute ComfyUI renders.")
review_app = typer.Typer(help="Review completed render attempts.")
compile_app = typer.Typer(help="Create manifests and assemble approved renders.")
production_app = typer.Typer(help="Inspect production-wide state and readiness.")
comfy_app = typer.Typer(help="Inspect the configured ComfyUI service.")
qc_app = typer.Typer(help="Discover persisted media quality reports.")
board_app = typer.Typer(help="Create rapid visual storyboards with Z-Image Turbo.")
app.add_typer(import_app, name="import")
app.add_typer(storyboard_app, name="storyboard")
app.add_typer(shot_app, name="shot")
app.add_typer(asset_app, name="asset")
app.add_typer(render_app, name="render")
app.add_typer(review_app, name="review")
app.add_typer(compile_app, name="compile")
app.add_typer(production_app, name="production")
app.add_typer(comfy_app, name="comfy")
app.add_typer(qc_app, name="qc")
app.add_typer(board_app, name="board")


@dataclass(frozen=True)
class AppState:
    root: Path
    database: Database
    output_format: str


@app.callback()
def callback(
    context: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help="Production project root."),
    database_path: Path = typer.Option(
        Path("storyboard.db"), "--db", help="SQLite file, relative to the project root by default."
    ),
    output_format: str = typer.Option("json", "--format", help="Output format: json or table."),
) -> None:
    resolved_root = root.resolve()
    if output_format not in ("json", "table"):
        raise typer.BadParameter("format must be json or table")
    resolved_database = database_path if database_path.is_absolute() else resolved_root / database_path
    context.obj = AppState(
        root=resolved_root,
        database=Database(resolved_database),
        output_format=output_format,
    )


def _state(context: typer.Context) -> AppState:
    return context.ensure_object(AppState)


def _service(context: typer.Context) -> StoryboardService:
    state = _state(context)
    return StoryboardService(state.database, state.root)


def _emit(context: typer.Context, value: Any) -> None:
    text = table_text(value) if _state(context).output_format == "table" else json_text(value)
    typer.echo(text)


def _execute(context: typer.Context, operation: Callable[[], Any]) -> None:
    try:
        _emit(context, operation())
    except StoryboardError as error:
        typer.echo(
            json_text({"code": error.code, "message": error.message, "details": error.details}),
            err=True,
        )
        raise typer.Exit(error.exit_code) from error
    except ValidationError as error:
        typer.echo(
            json_text({"code": "validation_error", "message": str(error), "details": {}}),
            err=True,
        )
        raise typer.Exit(ValidationFailure.exit_code) from error
    except (json.JSONDecodeError, ValueError) as error:
        typer.echo(
            json_text({"code": "validation_error", "message": str(error), "details": {}}),
            err=True,
        )
        raise typer.Exit(ValidationFailure.exit_code) from error
    except (OSError, sqlite3.Error) as error:
        typer.echo(
            json_text({"code": "system_error", "message": str(error), "details": {}}),
            err=True,
        )
        raise typer.Exit(1) from error


def _input_path(context: typer.Context, value: Path) -> Path:
    return value if value.is_absolute() else _state(context).root / value


@app.command("init")
def initialize(context: typer.Context) -> None:
    def operation() -> dict[str, Any]:
        state = _state(context)
        state.root.mkdir(parents=True, exist_ok=True)
        state.database.initialize()
        return {"initialized": True, "database": str(state.database.path)}

    _execute(context, operation)


@app.command("migrate")
def migrate(context: typer.Context) -> None:
    initialize(context)


@app.command("schema")
def schema(context: typer.Context, output: Path | None = typer.Option(None, "--output")) -> None:
    def operation() -> dict[str, Any]:
        result = ProjectSpec.model_json_schema()
        if output is not None:
            destination = _input_path(context, output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json_text(result) + "\n", encoding="utf-8")
        return result

    _execute(context, operation)


@app.command("doctor")
def doctor(context: typer.Context) -> None:
    def operation() -> dict[str, Any]:
        state = _state(context)
        return {
            "database_exists": state.database.path.is_file(),
            "ffmpeg": shutil.which("ffmpeg"),
            "ffprobe": shutil.which("ffprobe"),
            "ffmpeg_subtitles": bool(shutil.which("ffmpeg")) and ffmpeg_has_subtitles(),
            "comfy_url_configured": bool(ComfySettings.from_environment().base_url),
        }

    _execute(context, operation)


def _comfy_client() -> ComfyClient:
    return ComfyClient(ComfySettings.from_environment())


@comfy_app.command("ping")
def comfy_ping(context: typer.Context) -> None:
    _execute(context, lambda: _comfy_client().ping())


@comfy_app.command("queue")
def comfy_queue(context: typer.Context) -> None:
    _execute(context, lambda: _comfy_client().queue_status())


@comfy_app.command("preflight")
def comfy_preflight(context: typer.Context) -> None:
    def operation() -> dict[str, Any]:
        requirements = H3Adapter().requirements()
        return _comfy_client().preflight(nodes=requirements["nodes"], models=requirements["models"])

    _execute(context, operation)


@board_app.command("preflight")
def board_preflight(context: typer.Context) -> None:
    _execute(context, lambda: board_preflight_check(_comfy_client()))


@board_app.command("frame")
def board_frame(
    context: typer.Context,
    version: str,
    position: int,
    seed: int | None = typer.Option(None, "--seed"),
    width: int = typer.Option(1344, "--width"),
    height: int = typer.Option(768, "--height"),
    steps: int = typer.Option(8, "--steps"),
    plan_only: bool = typer.Option(False, "--plan-only"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        planned = service.plan_board_frame(
            version, position, seed=seed, settings={"width": width, "height": height, "steps": steps}
        )
        return planned if plan_only else BoardRunner(service, _comfy_client()).execute(planned["frame_id"])

    _execute(context, operation)


@board_app.command("render")
def board_render(
    context: typer.Context,
    version: str,
    width: int = typer.Option(1344, "--width"),
    height: int = typer.Option(768, "--height"),
    steps: int = typer.Option(8, "--steps"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        runner = BoardRunner(service, _comfy_client())
        rendered = []
        for shot in service.list_shots(version):
            completed = [
                f
                for f in service.list_board_frames(version, position=shot["position"])
                if f["revision_id"] == shot["revision_id"] and f["state"] == "completed"
            ]
            if completed:
                rendered.append(completed[-1])
                continue
            planned = service.plan_board_frame(
                version, shot["position"], settings={"width": width, "height": height, "steps": steps}
            )
            rendered.append(runner.execute(planned["frame_id"]))
        return {"version": version, "frames": rendered}

    _execute(context, operation)


@board_app.command("list")
def board_list(context: typer.Context, version: str, position: int | None = typer.Option(None, "--position")) -> None:
    _execute(context, lambda: _service(context).list_board_frames(version, position=position))


@board_app.command("build")
def board_build(context: typer.Context, version: str, width: int = 1344, height: int = 768, fps: int = 24) -> None:
    _execute(context, lambda: PreviewBuilder(_service(context)).build(version, PreviewSettings(width, height, fps)))


@board_app.command("create")
def board_create(
    context: typer.Context, version: str, width: int = 1344, height: int = 768, fps: int = 24, steps: int = 8
) -> None:
    def operation() -> dict[str, Any]:
        client = _comfy_client()
        check = board_preflight_check(client)
        if not check["ok"]:
            raise ValidationFailure("Z-Image Turbo preflight failed", details=check)
        service = _service(context)
        runner = BoardRunner(service, client)
        frames = []
        for shot in service.list_shots(version):
            existing = [
                f
                for f in service.list_board_frames(version, position=shot["position"])
                if f["revision_id"] == shot["revision_id"] and f["state"] == "completed"
            ]
            frames.append(
                existing[-1]
                if existing
                else runner.execute(
                    service.plan_board_frame(
                        version, shot["position"], settings={"width": width, "height": height, "steps": steps}
                    )["frame_id"]
                )
            )
        return {
            "frames": frames,
            "preview": PreviewBuilder(service).build(version, PreviewSettings(width, height, fps)),
        }

    _execute(context, operation)


@import_app.command("spec")
def import_spec(
    context: typer.Context,
    spec_file: Path,
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
) -> None:
    def operation() -> dict[str, Any]:
        path = _input_path(context, spec_file)
        spec = ProjectSpec.model_validate_json(path.read_text(encoding="utf-8"))
        return _service(context).import_spec(spec, idempotency_key=idempotency_key)

    _execute(context, operation)


@storyboard_app.command("list")
def storyboard_list(context: typer.Context) -> None:
    _execute(context, lambda: _service(context).list_versions())


@storyboard_app.command("audit")
def storyboard_audit(context: typer.Context, version: str) -> None:
    _execute(context, lambda: _service(context).audit_storyboard(version))


@production_app.command("status")
def production_status(
    context: typer.Context,
    version: str | None = typer.Option(None, "--version"),
) -> None:
    _execute(context, lambda: _service(context).production_status(version_name=version))


@storyboard_app.command("clone")
def storyboard_clone(
    context: typer.Context,
    source: str,
    name: str,
    title: str | None = typer.Option(None, "--title"),
) -> None:
    _execute(context, lambda: _service(context).clone_storyboard(source, name, title=title))


@storyboard_app.command("lock")
def storyboard_lock(context: typer.Context, version: str) -> None:
    _execute(context, lambda: _service(context).lock_storyboard(version))


@storyboard_app.command("archive")
def storyboard_archive(context: typer.Context, version: str) -> None:
    _execute(context, lambda: _service(context).archive_storyboard(version))


@storyboard_app.command("renumber")
def storyboard_renumber(
    context: typer.Context,
    version: str,
    step: int = typer.Option(10, "--step"),
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    _execute(
        context,
        lambda: _service(context).renumber_storyboard(version, step=step, expect_snapshot=expect_snapshot),
    )


@shot_app.command("list")
def shot_list(
    context: typer.Context,
    version: str,
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    _execute(context, lambda: _service(context).list_shots(version, include_archived=include_archived))


@shot_app.command("add")
def shot_add(
    context: typer.Context,
    version: str,
    shot_file: Path,
    after: int | None = typer.Option(None, "--after"),
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    def operation() -> dict[str, Any]:
        shot = ShotSpec.model_validate_json(_input_path(context, shot_file).read_text(encoding="utf-8"))
        return _service(context).add_shot(version, shot, after_position=after, expect_snapshot=expect_snapshot)

    _execute(context, operation)


@shot_app.command("revise")
def shot_revise(
    context: typer.Context,
    version: str,
    position: int,
    changes: str = typer.Option(..., "--changes", help="JSON object of fields to change."),
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    def operation() -> dict[str, Any]:
        payload = json.loads(changes)
        if not isinstance(payload, dict):
            raise ValidationFailure("--changes must contain a JSON object")
        return _service(context).revise_shot(version, position, payload, expect_snapshot=expect_snapshot)

    _execute(context, operation)


@shot_app.command("remove")
def shot_remove(
    context: typer.Context,
    version: str,
    position: int,
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    _execute(
        context,
        lambda: _service(context).remove_shot(version, position, expect_snapshot=expect_snapshot),
    )


@shot_app.command("move")
def shot_move(
    context: typer.Context,
    version: str,
    position: int,
    new_position: int,
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    _execute(
        context,
        lambda: _service(context).move_shot(
            version,
            position,
            new_position,
            expect_snapshot=expect_snapshot,
        ),
    )


@shot_app.command("bridge")
def shot_bridge(
    context: typer.Context,
    version: str,
    source_position: int,
    target_position: int,
    expect_snapshot: int | None = typer.Option(None, "--expect-snapshot"),
) -> None:
    _execute(
        context,
        lambda: _service(context).bridge_shots(
            version,
            source_position,
            target_position,
            expect_snapshot=expect_snapshot,
        ),
    )


@asset_app.command("add")
def asset_add(
    context: typer.Context,
    key: str,
    kind: AssetKind,
    path: str,
    title: str | None = typer.Option(None, "--title"),
) -> None:
    _execute(context, lambda: _service(context).add_asset(key, kind, path, title=title))


@asset_app.command("verify")
def asset_verify(context: typer.Context, key: str) -> None:
    _execute(context, lambda: _service(context).verify_asset(key))


@asset_app.command("link")
def asset_link(
    context: typer.Context,
    version: str,
    position: int,
    key: str,
    role: str = typer.Option("reference", "--role"),
    order: int = typer.Option(0, "--order"),
) -> None:
    _execute(
        context,
        lambda: _service(context).link_asset(version, position, key, role=role, order=order),
    )


def _render_runner(service: StoryboardService) -> RenderRunner:
    adapter = H3Adapter()
    return RenderRunner(
        service,
        _comfy_client(),
        {adapter.name: adapter},
    )


def _execute_or_return_plan(
    service: StoryboardService,
    planned: dict[str, Any],
    *,
    plan_only: bool,
    timeout_seconds: float,
    poll_seconds: float,
    no_wait: bool = False,
) -> dict[str, Any]:
    if plan_only or planned.get("state") != "planned":
        return planned
    runner = _render_runner(service)
    if no_wait:
        return runner.submit(planned["render_id"])
    return runner.execute(
        planned["render_id"],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


@render_app.command("shot")
def render_shot(
    context: typer.Context,
    version: str,
    position: int,
    seed: int | None = typer.Option(None, "--seed"),
    settings: str = typer.Option("{}", "--settings"),
    plan_only: bool = typer.Option(False, "--plan-only"),
    timeout_seconds: float = typer.Option(1800, "--timeout"),
    poll_seconds: float = typer.Option(3, "--poll-seconds"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    no_wait: bool = typer.Option(False, "--no-wait"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        parsed_settings = json.loads(settings)
        if not isinstance(parsed_settings, dict):
            raise ValidationFailure("--settings must contain a JSON object")
        planned = service.plan_render(
            version,
            position,
            seed=seed,
            settings=parsed_settings,
            idempotency_key=idempotency_key,
        )
        return _execute_or_return_plan(
            service,
            planned,
            plan_only=plan_only,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            no_wait=no_wait,
        )

    _execute(context, operation)


@render_app.command("retry")
def render_retry(
    context: typer.Context,
    render_id: str,
    plan_only: bool = typer.Option(False, "--plan-only"),
    no_wait: bool = typer.Option(False, "--no-wait"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        planned = service.retry_render(render_id)
        return _execute_or_return_plan(
            service, planned, plan_only=plan_only, timeout_seconds=1800, poll_seconds=3, no_wait=no_wait
        )

    _execute(context, operation)


@render_app.command("rerender")
def render_rerender(
    context: typer.Context,
    render_id: str,
    seed: int | None = typer.Option(None, "--seed"),
    plan_only: bool = typer.Option(False, "--plan-only"),
    no_wait: bool = typer.Option(False, "--no-wait"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        planned = service.rerender(render_id, seed=seed)
        return _execute_or_return_plan(
            service, planned, plan_only=plan_only, timeout_seconds=1800, poll_seconds=3, no_wait=no_wait
        )

    _execute(context, operation)


@render_app.command("status")
def render_status(context: typer.Context, render_id: str) -> None:
    _execute(context, lambda: _service(context).render_details(render_id))


@render_app.command("execute")
def render_execute(
    context: typer.Context,
    render_id: str,
    no_wait: bool = typer.Option(False, "--no-wait"),
    timeout_seconds: float = typer.Option(1800, "--timeout"),
    poll_seconds: float = typer.Option(3, "--poll-seconds"),
) -> None:
    def operation() -> dict[str, Any]:
        runner = _render_runner(_service(context))
        if no_wait:
            return runner.submit(render_id)
        return runner.execute(render_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)

    _execute(context, operation)


@render_app.command("wait")
def render_wait(
    context: typer.Context,
    render_id: str,
    timeout_seconds: float = typer.Option(1800, "--timeout"),
    poll_seconds: float = typer.Option(3, "--poll-seconds"),
) -> None:
    _execute(
        context,
        lambda: _render_runner(_service(context)).wait(
            render_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
        ),
    )


@render_app.command("list")
def render_list(
    context: typer.Context,
    version: str | None = typer.Option(None, "--version"),
    position: int | None = typer.Option(None, "--position"),
    state: str | None = typer.Option(None, "--state"),
) -> None:
    _execute(
        context,
        lambda: _service(context).list_renders(version_name=version, position=position, state=state),
    )


@render_app.command("qc")
def render_qc(context: typer.Context, render_id: str) -> None:
    state = _state(context)
    _execute(context, lambda: QualityInspector(state.database, state.root).inspect_render(render_id))


@render_app.command("reconcile")
def render_reconcile(
    context: typer.Context,
    render_id: str,
    timeout_seconds: float = typer.Option(1800, "--timeout"),
    poll_seconds: float = typer.Option(3, "--poll-seconds"),
) -> None:
    def operation() -> dict[str, Any]:
        service = _service(context)
        return _render_runner(service).reconcile(render_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)

    _execute(context, operation)


@review_app.command("approve")
def review_approve(
    context: typer.Context,
    render_id: str,
    reviewer: str | None = typer.Option(None, "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    _execute(
        context,
        lambda: _service(context).approve_render(render_id, reviewer=reviewer, notes=notes),
    )


@review_app.command("reject")
def review_reject(
    context: typer.Context,
    render_id: str,
    reviewer: str | None = typer.Option(None, "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    _execute(
        context,
        lambda: _service(context).reject_render(render_id, reviewer=reviewer, notes=notes),
    )


@review_app.command("history")
def review_history(context: typer.Context, render_id: str) -> None:
    _execute(context, lambda: _service(context).review_history(render_id))


def _compilation_settings(width: int, height: int, fps: int) -> CompilationSettings:
    return CompilationSettings(width=width, height=height, fps=fps)


@compile_app.command("manifest")
def compile_manifest(
    context: typer.Context,
    version: str,
    width: int = typer.Option(1920, "--width"),
    height: int = typer.Option(1080, "--height"),
    fps: int = typer.Option(24, "--fps"),
) -> None:
    state = _state(context)
    _execute(
        context,
        lambda: Compiler(state.database, state.root).create_manifest(
            version, _compilation_settings(width, height, fps)
        ),
    )


@compile_app.command("list")
def compile_list(
    context: typer.Context,
    version: str | None = typer.Option(None, "--version"),
    state: str | None = typer.Option(None, "--state"),
) -> None:
    _execute(context, lambda: _service(context).list_compilations(version_name=version, state=state))


@compile_app.command("selected")
def compile_selected(context: typer.Context, version: str) -> None:
    _execute(context, lambda: _service(context).approved_compilation(version))


@compile_app.command("build")
def compile_build(
    context: typer.Context,
    version: str,
    width: int = typer.Option(1920, "--width"),
    height: int = typer.Option(1080, "--height"),
    fps: int = typer.Option(24, "--fps"),
) -> None:
    state = _state(context)
    _execute(
        context,
        lambda: Compiler(state.database, state.root).build(version, _compilation_settings(width, height, fps)),
    )


@compile_app.command("qc")
def compile_qc(context: typer.Context, compilation_id: str) -> None:
    state = _state(context)
    _execute(context, lambda: QualityInspector(state.database, state.root).inspect_compilation(compilation_id))


@compile_app.command("approve")
def compile_approve(
    context: typer.Context,
    compilation_id: str,
    reviewer: str | None = typer.Option(None, "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    _execute(
        context,
        lambda: _service(context).approve_compilation(compilation_id, reviewer=reviewer, notes=notes),
    )


@compile_app.command("reject")
def compile_reject(
    context: typer.Context,
    compilation_id: str,
    reviewer: str | None = typer.Option(None, "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    _execute(
        context,
        lambda: _service(context).reject_compilation(compilation_id, reviewer=reviewer, notes=notes),
    )


@compile_app.command("history")
def compile_history(context: typer.Context, compilation_id: str) -> None:
    _execute(context, lambda: _service(context).compilation_review_history(compilation_id))


@qc_app.command("list")
def qc_list(
    context: typer.Context,
    render_id: str | None = typer.Option(None, "--render-id"),
    compilation_id: str | None = typer.Option(None, "--compilation-id"),
) -> None:
    _execute(
        context,
        lambda: _service(context).list_quality_reports(render_id=render_id, compilation_id=compilation_id),
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
