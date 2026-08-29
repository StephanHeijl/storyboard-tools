# Storyboard Tools Initial Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a GitHub-ready Python toolkit that safely authors, versions, renders, reviews, and assembles structured storyboards through an atomic agent-facing CLI.

**Architecture:** Typed Pydantic authoring models feed a transaction-oriented SQLite service layer. Thin CLI commands call the same service API used by Python consumers; ComfyUI and ffmpeg run outside short database transactions and record immutable provenance on every state transition.

**Tech Stack:** Python 3.11+, Pydantic 2, Typer, httpx, SQLite, ffmpeg/ffprobe, pytest, Ruff, mypy, build.

---

### Task 1: Package Skeleton and Authoring Schema

**Files:**
- Create: `pyproject.toml`
- Create: `src/storyboardctl/__init__.py`
- Create: `src/storyboardctl/models.py`
- Create: `src/storyboardctl/errors.py`
- Create: `tests/test_models.py`

**Step 1: Write failing schema tests**

Cover valid project/storyboard/shot composition, JSON round trips, explicit asset ordering, first/last-frame roles, music relationships, positive durations, safe integer positions, and JSON Schema generation.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_models.py -q`

Expected: collection fails because `storyboardctl.models` does not exist.

**Step 3: Implement the minimal typed schema**

Use strict Pydantic models with `extra="forbid"`. Define `ProjectSpec`, `StoryboardSpec`, `ShotSpec`, `AssetSpec`, `ShotAssetSpec`, `ShotLinkSpec`, `MusicCueSpec`, enums, and validators. Add `storyboardctl schema` support later through `ProjectSpec.model_json_schema()`.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_models.py -q`

Expected: all schema tests pass.

**Step 5: Commit**

Run: `git add pyproject.toml src/storyboardctl tests/test_models.py && git commit -m "feat: add typed storyboard authoring schema"`

### Task 2: SQLite Migrations and Transaction Foundation

**Files:**
- Create: `src/storyboardctl/database.py`
- Create: `src/storyboardctl/migrations.py`
- Create: `src/storyboardctl/paths.py`
- Create: `tests/test_database.py`
- Create: `tests/test_paths.py`

**Step 1: Write failing database and path tests**

Cover initialization, idempotent migrations, foreign keys, WAL mode, busy timeout, rollback on exceptions, event idempotency uniqueness, normalized project-relative paths, symlink escape refusal, and SHA-256 hashing.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_database.py tests/test_paths.py -q`

Expected: imports fail because the modules do not exist.

**Step 3: Implement migration and transaction code**

Create the complete v1 relational schema from the design. Expose a `Database` context manager with read transactions and `BEGIN IMMEDIATE` writes. Implement project-root-safe path helpers without deleting or moving files.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_database.py tests/test_paths.py -q`

Expected: all database and path tests pass.

**Step 5: Commit**

Run: `git add src/storyboardctl/database.py src/storyboardctl/migrations.py src/storyboardctl/paths.py tests && git commit -m "feat: add transactional SQLite foundation"`

### Task 3: Storyboard Import, Versioning, and Shot Operations

**Files:**
- Create: `src/storyboardctl/service.py`
- Create: `tests/test_storyboards.py`
- Create: `tests/test_import.py`

**Step 1: Write failing domain tests**

Cover atomic spec import, automatic `10,20,30` numbering, cloning with shared revisions, approval-compatible revision identity, content edits creating immutable revisions, insertion midpoint selection, exhausted-gap conflicts, explicit renumbering, optimistic snapshot conflicts, soft removal, locked-version mutation refusal, and idempotent retries.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_storyboards.py tests/test_import.py -q`

Expected: imports fail because `StoryboardService` does not exist.

**Step 3: Implement minimal domain operations**

Implement production initialization/import, version list/show/clone/lock/archive/renumber, shot list/show/add/revise/move/remove, append-only events, and typed result dictionaries. Keep all mutations transactional and require draft status.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_storyboards.py tests/test_import.py -q`

Expected: all domain tests pass.

**Step 5: Commit**

Run: `git add src/storyboardctl/service.py tests && git commit -m "feat: add versioned storyboard operations"`

### Task 4: Assets, Music, Renders, and Reviews

**Files:**
- Modify: `src/storyboardctl/service.py`
- Create: `src/storyboardctl/rendering.py`
- Create: `tests/test_assets.py`
- Create: `tests/test_renders.py`
- Create: `tests/test_reviews.py`

**Step 1: Write failing lifecycle tests**

Cover asset registration and ordered links, hash verification, reusable music links, unique monotonic render attempts, collision-proof output paths, allowed render state transitions, exact retry seed/settings, rerender seed replacement, one approved render per revision, rejection history, supersession, and automatic approval carryover/invalidation through shared/new revision IDs.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_assets.py tests/test_renders.py tests/test_reviews.py -q`

Expected: tests fail on missing operations.

**Step 3: Implement minimal lifecycle services**

Add asset/music operations, render planning/state transitions, review operations, current selection, and deterministic unique path generation. Enforce file integrity and revision compatibility.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_assets.py tests/test_renders.py tests/test_reviews.py -q`

Expected: all lifecycle tests pass.

**Step 5: Commit**

Run: `git add src/storyboardctl tests && git commit -m "feat: track assets renders and approvals"`

### Task 5: ComfyUI Client and Adapter Boundary

**Files:**
- Create: `src/storyboardctl/comfy/__init__.py`
- Create: `src/storyboardctl/comfy/client.py`
- Create: `src/storyboardctl/comfy/adapters.py`
- Create: `src/storyboardctl/comfy/h3.py`
- Create: `tests/test_comfy.py`
- Create: `tests/test_h3_adapter.py`

**Step 1: Write failing fake-server tests**

Cover upload MIME handling, prompt submission, history polling, execution errors, timeout, recursive video discovery, safe download, redacted configuration, adapter registration, and a small API-format H3 workflow built only from structured shot data.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_comfy.py tests/test_h3_adapter.py -q`

Expected: imports fail because ComfyUI modules do not exist.

**Step 3: Implement the generic client and adapter**

Use `httpx.Client` with bounded retries and timeouts. Define a workflow adapter protocol and registry. Implement an H3 adapter with configurable model filenames and no production-specific address, prompt macros, names, or paths.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_comfy.py tests/test_h3_adapter.py -q`

Expected: all ComfyUI tests pass.

**Step 5: Commit**

Run: `git add src/storyboardctl/comfy tests && git commit -m "feat: add ComfyUI rendering integration"`

### Task 6: Compilation Manifests and Full ffmpeg Assembly

**Files:**
- Create: `src/storyboardctl/compiler.py`
- Create: `tests/test_compiler.py`
- Create: `tests/test_ffmpeg.py`

**Step 1: Write failing compilation tests**

Cover ordered approved-render selection, missing/stale approval failures, immutable numbered snapshots, source hash provenance, intended trim duration, short-source refusal, safe subprocess argument construction, and an end-to-end tiny two-shot ffmpeg build when available.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_compiler.py tests/test_ffmpeg.py -q`

Expected: imports fail because the compiler does not exist.

**Step 3: Implement manifests and assembly**

Create a compilation record before work begins, write an immutable JSON manifest, probe inputs with ffprobe, normalize each clip to a temporary directory, concatenate with ffmpeg, hash the completed output, and update compilation status. Keep music metadata in the manifest without mixing it.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_compiler.py tests/test_ffmpeg.py -q`

Expected: all compiler tests pass or ffmpeg-only tests skip with a reason if unavailable.

**Step 5: Commit**

Run: `git add src/storyboardctl/compiler.py tests && git commit -m "feat: assemble approved storyboard renders"`

### Task 7: Agent-Facing CLI

**Files:**
- Create: `src/storyboardctl/cli.py`
- Create: `src/storyboardctl/output.py`
- Create: `tests/test_cli.py`

**Step 1: Write failing CLI tests**

Cover `init`, `schema`, `import spec`, storyboard and shot operations, asset/music links, render planning/status, reviews, compilation, JSON stdout, JSON error stderr, table format, stable exit codes, non-interactive behavior, idempotency keys, and optimistic snapshots.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_cli.py -q`

Expected: CLI import or command lookup fails.

**Step 3: Implement thin command handlers**

Use Typer without Rich exception pages. Centralize serialization and exception mapping. Commands must delegate business rules to services and remain scriptable.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_cli.py -q`

Expected: all CLI tests pass.

**Step 5: Commit**

Run: `git add src/storyboardctl/cli.py src/storyboardctl/output.py tests/test_cli.py pyproject.toml && git commit -m "feat: expose agent-safe storyboard CLI"`

### Task 8: Example, Documentation, Packaging, and Release Verification

**Files:**
- Create: `examples/moonlight_delivery.py`
- Create: `examples/moonlight_delivery.json`
- Create: `README.md`
- Create: `LICENSE`
- Create: `CONTRIBUTING.md`
- Create: `docs/schema.md`
- Create: `tests/test_example.py`

**Step 1: Write failing example test**

Require the fictional Python example to generate the committed JSON exactly and require that JSON to import into a fresh database.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_example.py -q`

Expected: example files are missing.

**Step 3: Add the example and project documentation**

Document installation, authoring, command contracts, versioning, approvals, ComfyUI configuration, compilation, safe paths, migrations, and development. Include a command cookbook for agents and the MIT license.

**Step 4: Run full verification**

Run:

```bash
python -m pytest -q
python -m ruff check .
python -m mypy src
python -m build
python -m storyboardctl.cli --help
rg -n -i '192\.168\.|/Users/|api[_-]?key|password' README.md LICENSE CONTRIBUTING.md docs examples src tests pyproject.toml
git status --short
```

Expected: tests, lint, types, build, and smoke test pass; sensitive/project-specific scan has no matches; only deliberate final changes are present.

**Step 5: Commit**

Run: `git add README.md LICENSE CONTRIBUTING.md docs examples tests/test_example.py && git commit -m "docs: prepare initial open-source release"`

**Step 6: Request code review and address findings**

Use `superpowers:requesting-code-review`, verify the complete diff and rerun the full verification suite after any corrections.
