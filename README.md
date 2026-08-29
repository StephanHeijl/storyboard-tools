# Storyboard Tools

Storyboard Tools turns a video production into structured, versioned data that humans and agents can change without losing, swapping, or silently overwriting shots.

It provides:

- a typed Python authoring schema designed for LLM structured output;
- one portable SQLite metadata database per production;
- immutable shot revisions and cheap storyboard snapshots;
- reusable image, audio, video, first-frame, and last-frame relationships;
- collision-proof render attempts with seeds, prompts, workflows, outputs, and reviews;
- a ComfyUI client and configurable MiniMax H3 workflow adapter;
- deterministic compilation manifests and full `ffmpeg` assembly;
- JSON-first CLI commands with stable error codes and no hidden prompts.

Large media files remain ordinary project-relative files. SQLite contains paths, hashes, metadata, relationships, and provenance—not binary media or secrets.

## Installation

Storyboard Tools requires Python 3.11 or newer. Full assembly also requires `ffmpeg` and `ffprobe` on `PATH`.

```bash
git clone https://github.com/your-org/storyboard-tools.git
cd storyboard-tools
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
storyboardctl --help
```

For development:

```bash
python -m pip install -e '.[dev]'
pytest
```

## Start a production

Create a directory for the production, generate or write a structured spec, then import it:

```bash
mkdir moonlight-production
cd moonlight-production
storyboardctl init
storyboardctl schema --output project-schema.json
storyboardctl import spec ../storyboard-tools/examples/moonlight_delivery.json \
  --idempotency-key initial-import
storyboardctl shot list v1
```

The bundled [Python example](examples/moonlight_delivery.py) composes the same JSON with Pydantic models. It is intentionally tiny and fictional.

```python
from pathlib import Path

from storyboardctl.models import ProjectSpec, ShotSpec, StoryboardSpec

project = ProjectSpec(
    slug="sample-film",
    title="Sample Film",
    storyboard=StoryboardSpec(
        name="v1",
        title="First Cut",
        shots=[
            ShotSpec(
                key="arrival",
                title="Arrival",
                description="A courier arrives.",
                prompt="Wide shot of a courier arriving at dawn.",
                duration_seconds=4,
            )
        ],
    ),
)

Path("project.json").write_text(project.model_dump_json(indent=2))
```

Unknown fields, broken asset/shot/music references, duplicate keys, unsafe paths, invalid durations, and conflicting positions fail validation before the database changes.

## The versioning model

A conceptual shot has a stable UUID. Its content is an immutable revision. A storyboard version is an ordered snapshot selecting revisions:

```text
shot UUID ── revision 1 ── used by v1 and v2 ── approved render carries over
          └─ revision 2 ── used only by edited v3 ── requires a new approval
```

Clone a version before restructuring it:

```bash
storyboardctl storyboard clone v1 short-cut --title 'Short Cut'
storyboardctl shot remove short-cut 40 --expect-snapshot 0
```

Shots begin at `10, 20, 30…`. Inserting after `10` chooses `15` when `20` follows. The tool never renumbers implicitly:

```bash
storyboardctl shot add v1 new-shot.json --after 10
storyboardctl storyboard renumber v1 --step 10 --expect-snapshot 4
```

Stable IDs mean explicit renumbering cannot detach assets, links, renders, or approvals. Mutations return the new storyboard snapshot. Pass it back with `--expect-snapshot` when concurrent agents may work on the same version.

## Render and review workflow

Connection details are runtime-only:

```bash
export STORYBOARDCTL_COMFY_URL='http://127.0.0.1:8188'
export STORYBOARDCTL_COMFY_TOKEN='optional-bearer-token'
```

Queue, wait for, and download a shot through ComfyUI:

```bash
storyboardctl render shot v1 20 \
  --settings '{"width":1344,"height":768,"steps":20}'
```

The command creates a render row before the network request, uploads hash-verified linked assets in declared order, writes the exact workflow snapshot, records the ComfyUI prompt ID and state transitions, downloads to a unique filename, probes duration, and hashes the result.

Useful atomic operations:

```bash
storyboardctl render shot v1 20 --seed 123 --plan-only
storyboardctl render status RENDER_ID
storyboardctl render retry RENDER_ID
storyboardctl render rerender RENDER_ID --seed 456
storyboardctl review approve RENDER_ID --reviewer agent-qa --notes 'Identity stable'
storyboardctl review reject RENDER_ID --notes 'Continuity jump at final frame'
storyboardctl review history RENDER_ID
```

`retry` preserves the exact seed and settings. `rerender` keeps the settings and selects a new random seed unless one is supplied. Every attempt gets a monotonic attempt number and UUID fragment; files are never overwritten.

Approving a completed render selects it for that exact shot revision and supersedes the earlier selection without deleting review history. Storyboard clones share that approval while they share the revision. Editing prompt, duration, render settings, assets, frames, or other render-relevant data creates a revision with no approval.

## Assets and music

Paths are normalized relative to the production root. Registration records a SHA-256 hash:

```bash
storyboardctl asset add courier image assets/courier.png
storyboardctl asset verify courier
storyboardctl asset link v1 20 courier --role reference --order 0
storyboardctl asset link v1 20 opening-frame --role first_frame --order 0
```

Structured specs can define reusable music cues and associate them with shots as `starts_here`, `continues`, or `associated`. Cue metadata is preserved in the database and compilation manifest. Version 0.1 deliberately does not mix music; that remains a separate audio-finishing concern.

## Compile approved renders

Generate a pinned manifest without encoding:

```bash
storyboardctl compile manifest v1 --width 1920 --height 1080 --fps 24
```

Or assemble the full cut:

```bash
storyboardctl compile build v1 --width 1920 --height 1080 --fps 24
```

Compilation stops before `ffmpeg` if any active shot lacks an approved compatible render, an output hash is stale, or a render is shorter than its intended trim duration. Inputs are normalized before concatenation. Outputs are monotonic snapshots such as `assembly/v1/cut_001.mp4`; later builds produce `cut_002.mp4` rather than replacing history.

## Agent contract

JSON is written to stdout by default. Expected failures write one JSON object to stderr and use stable exit statuses:

| Exit | Meaning |
|---:|---|
| 0 | Success |
| 1 | System or unexpected SQLite/file error |
| 2 | Input validation failure |
| 3 | Resource not found |
| 4 | State, snapshot, or uniqueness conflict |
| 5 | ComfyUI, ffmpeg, ffprobe, or other external-service failure |
| 6 | File/path/hash integrity failure |

Use a unique `--idempotency-key` for import retries. Commands never ask questions. For people inspecting state, put `--format table` before the command:

```bash
storyboardctl --format table storyboard list
```

## Safety and storage

- Foreign keys, uniqueness constraints, short transactions, WAL mode, and a busy timeout protect the database.
- Version mutations are limited to drafts and support optimistic snapshot checks.
- Normal removal archives metadata. The initial release performs no media-file deletion.
- Symlinks and `..` cannot escape the production root.
- Asset and render hashes are checked at consumption boundaries.
- Network calls never hold a database write transaction.
- Tokens are read from the environment and redacted from configuration representations.
- Databases, local configuration, renders, and assemblies are ignored by the repository template.

See [Schema and invariants](docs/schema.md) for the relational model and [Contributing](CONTRIBUTING.md) for development checks.

## License

MIT. See [LICENSE](LICENSE).

