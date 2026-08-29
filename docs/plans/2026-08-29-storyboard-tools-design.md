# Storyboard Tools Design

## Purpose

Storyboard Tools is an open-source, agent-facing Python toolkit for managing a video production as structured, versioned data. It replaces freehand Markdown parsing, render-directory guessing, and ad-hoc approval maps with a portable SQLite metadata database and a small set of atomic CLI operations.

The database is the source of truth for storyboard structure, shot revisions, ordered asset relationships, ComfyUI jobs, render attempts, reviews, and compilation selection. Large media remains in ordinary project-relative files. Every stored file reference includes a SHA-256 digest when the file is available, so accidental replacement can be detected without embedding media in SQLite.

The first release provides a typed Python authoring schema. Humans and language models can compose `ProjectSpec`, `StoryboardSpec`, and `ShotSpec` objects, validate them before touching the database, serialize them to JSON, and import the result transactionally. A tiny fictional example demonstrates the format; no Duinrell data is copied.

The package and command are named `storyboardctl`. It targets Python 3.11 or newer, uses SQLite directly rather than an ORM, emits JSON on stdout by default, reserves stderr for diagnostics, never prompts unless explicitly asked, and uses stable exit statuses. It ships under the MIT license.

## Architecture

The system has five layers:

1. `schema`: frozen typed dataclasses for safe authoring and JSON interchange, with validation that reports precise field paths.
2. `db`: schema migrations, transaction management, row mapping, integrity constraints, and an append-only event log.
3. `service`: domain operations such as cloning a storyboard, revising a shot, linking an asset, approving a render, and selecting a compilation. All mutations occur here inside explicit transactions.
4. `comfy`: a generic ComfyUI HTTP client plus adapter protocol. An included MiniMax H3 adapter is derived from the useful generic concepts in the existing project, but contains no production-specific prompts, paths, names, addresses, or credentials.
5. `cli`: thin command handlers that parse arguments, call one service operation, serialize a result, and map domain errors to stable exit codes.

SQLite uses foreign keys, WAL mode, a busy timeout, explicit migrations, UTC timestamps, and uniqueness constraints as the final guard against races. Mutating CLI operations accept an optional idempotency key so an agent retry cannot create duplicate shots, jobs, or approvals. Human-facing shot numbers are mutable ordering labels; immutable UUIDs and revision IDs are used for relationships.

## Storyboard and Shot Versioning

A production database contains multiple named storyboard versions. A storyboard version has a lifecycle of `draft`, `locked`, or `archived`. Cloning creates a new draft snapshot whose ordered entries initially point at the same immutable shot revisions as the source. Approved renders therefore carry over automatically without copying rows.

A conceptual shot has a stable UUID. Its content lives in immutable shot revisions. Editing any render-relevant field creates a new revision and changes only the selected storyboard version's entry. Other versions continue to reference the earlier revision and remain reproducible. Metadata-only review notes use separate tables and do not create content revisions.

Each storyboard version owns ordered `version_shots` entries. Default labels are `10`, `20`, `30`, and so on. Insertion chooses an available integer between neighbors; if no integer remains, the command returns a conflict and asks for an explicit `storyboard renumber --step 10`. Renumbering changes labels only, never UUIDs, links, renders, or revisions.

Removing a shot archives its version entry rather than deleting the conceptual shot. `--purge` is a separate guarded operation and refuses to delete referenced records. Locked storyboard versions cannot be mutated; they must be cloned first. A version snapshot number increments on every successful mutation and is included in output, allowing agents to use optimistic `--expect-snapshot` checks and avoid overwriting concurrent changes.

## Data Model

Core tables are:

- `schema_migrations`: installed migration versions.
- `production`: the single production identity and safe relative configuration.
- `storyboard_versions`: named snapshots and their lifecycle state.
- `shots`: stable conceptual shot UUIDs.
- `shot_revisions`: immutable content including description, prompt, intended duration, render mode, seed policy, and JSON adapter settings.
- `version_shots`: version membership, integer display number, revision selection, and archive state.
- `assets`: reusable project-relative files with kind, media type, hash, and optional duration.
- `shot_assets`: ordered roles such as reference image, first frame, last frame, attachment, or source audio.
- `shot_links`: semantic relationships between stable shots, including continuity, derives-first-frame, and derives-last-frame.
- `music_cues` and `shot_music`: reusable audio assets with `starts_here`, `continues`, or `associated`, plus optional offset, gain, and fade metadata.
- `renders`: one row per attempt, including revision, monotonic attempt number, state, seed, prompt snapshot, settings, workflow snapshot path, ComfyUI prompt ID, output path/hash, measured duration, and failure details.
- `render_reviews`: append-only approval/rejection history.
- `approved_renders`: the single currently selected approved render per shot revision.
- `events`: append-only audit records for every domain mutation and idempotency key.

The database stores paths, hashes, and metadata—not media blobs or credentials. Render output names include a filesystem-safe shot label, immutable render ID fragment, and zero-padded attempt number. Re-renders never overwrite prior media.

## Rendering and Review Flow

`render shot 30` resolves the active storyboard entry, validates all linked files and hashes, asks the configured adapter to construct an API-format ComfyUI workflow, writes that immutable workflow snapshot, records a queued render attempt, submits it, records the returned prompt ID, polls history, downloads the output to a unique project-relative path, probes its media duration, hashes it, and marks the render complete. Each state transition is an individual short database transaction; network calls never hold a database lock.

`render retry RENDER_ID` repeats the exact prompt, workflow settings, and seed. `render rerender RENDER_ID` copies the configuration but selects a fresh random seed unless `--seed` is supplied. Failed or interrupted jobs remain inspectable and can be reconciled by prompt ID. ComfyUI connection details come from environment variables or an ignored local TOML file, never from the production database.

Reviews are append-only. Approving a completed render selects it as the approved render for that exact shot revision and supersedes any prior selection without deleting history. Rejecting records a reason and unselects that render if necessary. A render for an old revision cannot accidentally satisfy a changed shot. Cloned storyboards share approvals because they share revision IDs.

The ComfyUI client handles image upload, prompt submission, history polling, output discovery, and download. Adapters own model-specific workflow construction. The included H3 adapter is an example; users can register additional adapters without modifying the database or service layer.

## Compilation and Snapshots

`compile manifest` walks a selected storyboard version in numeric order and requires one compatible approved render for every active entry. The resulting immutable compilation snapshot records the storyboard version and snapshot number, ordered render IDs, exact source hashes, intended trim durations, transitions, and output settings. Missing approvals, stale hashes, or duplicate positions fail before `ffmpeg` starts.

`compile build` creates that manifest and performs full assembly with `ffmpeg`. Video sources are normalized to the requested canvas, frame rate, pixel format, video codec, and audio format before concatenation. Each source is trimmed to the shot's intended duration; shorter renders cause a validation failure rather than silent timeline drift. The release does not mix reusable music cues. Music relationships remain available in the manifest for a later dedicated audio-mixing layer.

Compilation filenames use a monotonic version per storyboard, for example `assembly/v2/cut_003.mp4`, and never overwrite an earlier snapshot. The manifest lives beside the output and provides reproducible provenance. A later build against the same storyboard may select newer approved renders and becomes `cut_004`; old cuts and manifests remain intact until explicitly archived or purged.

## CLI Surface

The initial command families are:

- `init`, `migrate`, `doctor`
- `import spec`, `export spec`
- `storyboard list|show|clone|lock|archive|renumber`
- `shot list|show|add|revise|move|remove`
- `asset list|add|verify|link|unlink`
- `music add|link|unlink`
- `render shot|retry|rerender|status|wait|list|reconcile`
- `review approve|reject|history`
- `compile manifest|build|list`

Every successful command prints one JSON object. Lists contain stable IDs as well as human labels. Errors use a JSON object on stderr with `code`, `message`, and optional `details`; expected validation, conflict, missing-resource, external-service, and system failures have distinct exit statuses. `--format table` provides compact interactive views. Mutations support `--idempotency-key`, and version mutations support `--expect-snapshot`.

The Python service API mirrors these operations and returns typed values. The CLI does not duplicate business rules. This keeps calls minimal for agents while ensuring the same validation and atomicity whether the caller uses Python or a subprocess.

## Error Handling and Safety

Inputs are validated before mutation. Paths must be relative, normalized, remain within the project root after resolution, and not traverse symlinks outside it. Existing files are hashed when registered and verified before rendering or compilation. Database constraints enforce valid states and uniqueness; service checks provide clearer errors before those constraints are reached.

No command removes a media file during normal archival. Purge operations require `--purge`, refuse when references exist, and report exactly which metadata was deleted. The tool never deletes arbitrary external paths. Secrets are read at runtime and are redacted from diagnostics. Workflow snapshots are scrubbed through an adapter hook before persistence.

Network operations use bounded timeouts and retries. Submission uses idempotency records locally; reconciliation prevents a lost CLI connection from creating an untracked duplicate job. SQLite transactions are short, use `BEGIN IMMEDIATE` for contested writes, and expose optimistic snapshot conflicts rather than silently accepting stale updates.

## Testing and Release Quality

Development follows red-green-refactor. Unit tests cover schema validation, migrations, numbering, clone/revision behavior, approval carryover and invalidation, idempotency, safe paths, state transitions, manifest selection, and deterministic naming. Integration tests run against temporary project directories and SQLite databases.

A local fake ComfyUI HTTP server verifies upload, submission, polling, failure, reconciliation, and download without a network dependency. `ffmpeg` integration tests use tiny generated color clips when the executable is available and otherwise skip with an explicit reason. CLI tests invoke the real entry point and assert JSON stdout, JSON stderr, and exit codes.

Release verification includes the full test suite, type checking, linting, package build, installation into an isolated environment, example import, CLI smoke tests, database integrity checks, and a scan ensuring the repository contains no Duinrell strings, private IP addresses, absolute workspace paths, credentials, databases, renders, or generated media.
