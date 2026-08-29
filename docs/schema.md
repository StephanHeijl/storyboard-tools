# Schema and invariants

## Authoring schema

`ProjectSpec.model_json_schema()` is the canonical JSON Schema. The CLI writes it with:

```bash
storyboardctl schema --output project-schema.json
```

This is suitable for an LLM structured-output request. The top-level model contains production identity, reusable assets, reusable music cues, and one initial storyboard. `extra="forbid"` is applied at every level so a misspelled field fails instead of disappearing.

`ShotSpec` contains:

| Field | Purpose |
|---|---|
| `key` | Stable human-authored identifier within the production |
| `position` | Optional positive integer; omitted positions become `10,20,30…` |
| `title`, `description` | Human/agent navigation and intent |
| `prompt`, `negative_prompt` | Exact generation input |
| `duration_seconds` | Intended edit duration, distinct from generated-media duration |
| `render_mode` | `text_to_video`, `image_to_video`, `reference_to_video`, or `custom` |
| `seed` | Optional default seed; every attempt records the resolved seed |
| `adapter` | Workflow adapter name, `h3` by default |
| `render_settings` | Adapter settings such as dimensions, frames, and steps |
| `assets` | Ordered asset relationships and frame roles |
| `links` | Relationships to stable shot keys |
| `music` | Cue key to relationship mapping |

The Python models and JSON schema are two views of the same Pydantic definitions. Import runs in one `BEGIN IMMEDIATE` transaction.

## Relational model

The SQLite database has one `production` row and any number of storyboard versions. The principal relationship is:

```text
production
 ├─ assets ── music_cues
 ├─ shots ── shot_revisions ── renders ── render_reviews
 │                │                 └──── approved_renders
 │                ├─ shot_assets
 │                ├─ shot_links
 │                └─ shot_music
 └─ storyboard_versions ── version_shots
                             └─ compilations ── compilation_items
```

`shots` are stable identities. `shot_revisions` are immutable render inputs. `version_shots` selects one revision and a display position for a version. Therefore a clone can share all approvals without duplicating data, while one edited shot can diverge without changing the source version.

## Required invariants

- A production slug, version name, shot key, asset key, and cue key are unique in their scopes.
- Active shot positions are unique per storyboard version.
- Revision numbers and render attempt numbers increase monotonically per parent.
- Revision content hashes prevent accidental duplicate revisions.
- Exactly one render can be selected for a shot revision.
- A selected render must belong to the selected revision; the service enforces completion before review.
- Compilation items pin shot, revision, render, hash, order, and trim duration.
- An idempotency key appears at most once in the append-only event log.
- Archived entries remain available for provenance but are excluded from active listings and compilation.

## State machines

Storyboard versions move from `draft` to `locked` or `archived`. Only drafts can change.

Renders move through:

```text
planned → queued → running → completed
   └────────┴────────┴──────→ failed | cancelled
```

Terminal render states cannot transition again. Review decisions do not rewrite render state.

Compilations move through `planned → building → completed`, with `failed` as a terminal error state. Their filenames and database numbers are never reused.

## Paths and secrets

Stored paths are POSIX-style and project-relative. Resolution checks the real project root after following symlinks. Existing registered media receives a SHA-256 digest; render and compilation outputs are hashed on completion.

The database does not contain ComfyUI URLs, bearer tokens, or large binaries. Runtime configuration uses `STORYBOARDCTL_COMFY_URL` and `STORYBOARDCTL_COMFY_TOKEN`.

