from __future__ import annotations

MIGRATION_1 = """
CREATE TABLE production (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    root_path TEXT NOT NULL DEFAULT '.',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE storyboard_versions (
    id TEXT PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'locked', 'archived')),
    snapshot INTEGER NOT NULL DEFAULT 0 CHECK (snapshot >= 0),
    parent_version_id TEXT REFERENCES storyboard_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE (production_id, name)
);

CREATE TABLE shots (
    id TEXT PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE RESTRICT,
    shot_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE (production_id, shot_key)
);

CREATE TABLE shot_revisions (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
    render_mode TEXT NOT NULL,
    seed INTEGER CHECK (seed IS NULL OR seed >= 0),
    adapter TEXT NOT NULL,
    render_settings_json TEXT NOT NULL DEFAULT '{}',
    notes_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (shot_id, revision_number),
    UNIQUE (shot_id, content_hash)
);

CREATE TABLE version_shots (
    version_id TEXT NOT NULL REFERENCES storyboard_versions(id) ON DELETE RESTRICT,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position > 0),
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (version_id, shot_id)
);
CREATE UNIQUE INDEX version_shots_active_position
    ON version_shots(version_id, position) WHERE archived_at IS NULL;

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE RESTRICT,
    asset_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    media_type TEXT,
    sha256 TEXT,
    duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE (production_id, asset_key),
    UNIQUE (production_id, path)
);

CREATE TABLE shot_assets (
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    notes TEXT,
    PRIMARY KEY (revision_id, role, sort_order),
    UNIQUE (revision_id, asset_id, role)
);

CREATE TABLE shot_links (
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE CASCADE,
    target_shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (revision_id, target_shot_id, kind)
);

CREATE TABLE music_cues (
    id TEXT PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE RESTRICT,
    cue_key TEXT NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE (production_id, cue_key)
);

CREATE TABLE shot_music (
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE CASCADE,
    music_cue_id TEXT NOT NULL REFERENCES music_cues(id) ON DELETE RESTRICT,
    relationship TEXT NOT NULL CHECK (relationship IN ('starts_here', 'continues', 'associated')),
    offset_seconds REAL NOT NULL DEFAULT 0,
    gain_db REAL,
    fade_in_seconds REAL CHECK (fade_in_seconds IS NULL OR fade_in_seconds >= 0),
    fade_out_seconds REAL CHECK (fade_out_seconds IS NULL OR fade_out_seconds >= 0),
    PRIMARY KEY (revision_id, music_cue_id)
);

CREATE TABLE renders (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    state TEXT NOT NULL CHECK (
        state IN ('planned', 'queued', 'running', 'timed_out', 'completed', 'failed', 'cancelled')
    ),
    seed INTEGER NOT NULL CHECK (seed >= 0),
    prompt_snapshot TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    workflow_path TEXT,
    comfy_prompt_id TEXT,
    output_path TEXT,
    output_sha256 TEXT,
    duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    error_message TEXT,
    source_render_id TEXT REFERENCES renders(id) ON DELETE RESTRICT,
    replay_workflow INTEGER NOT NULL DEFAULT 0 CHECK (replay_workflow IN (0, 1)),
    created_at TEXT NOT NULL,
    queued_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE (revision_id, attempt_number),
    UNIQUE (comfy_prompt_id),
    UNIQUE (output_path)
);

CREATE TABLE render_reviews (
    id TEXT PRIMARY KEY,
    render_id TEXT NOT NULL REFERENCES renders(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reviewer TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE approved_renders (
    revision_id TEXT PRIMARY KEY REFERENCES shot_revisions(id) ON DELETE RESTRICT,
    render_id TEXT NOT NULL UNIQUE REFERENCES renders(id) ON DELETE RESTRICT,
    review_id TEXT NOT NULL REFERENCES render_reviews(id) ON DELETE RESTRICT,
    selected_at TEXT NOT NULL
);

CREATE TABLE compilations (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES storyboard_versions(id) ON DELETE RESTRICT,
    compilation_number INTEGER NOT NULL CHECK (compilation_number > 0),
    storyboard_snapshot INTEGER NOT NULL CHECK (storyboard_snapshot >= 0),
    state TEXT NOT NULL CHECK (state IN ('planned', 'building', 'completed', 'failed')),
    manifest_path TEXT NOT NULL UNIQUE,
    output_path TEXT,
    output_sha256 TEXT,
    settings_json TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (version_id, compilation_number)
);

CREATE TABLE compilation_items (
    compilation_id TEXT NOT NULL REFERENCES compilations(id) ON DELETE RESTRICT,
    item_order INTEGER NOT NULL CHECK (item_order >= 0),
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES shot_revisions(id) ON DELETE RESTRICT,
    render_id TEXT NOT NULL REFERENCES renders(id) ON DELETE RESTRICT,
    source_sha256 TEXT NOT NULL,
    duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
    PRIMARY KEY (compilation_id, item_order)
);

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX events_idempotency
    ON events(production_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
"""


MIGRATIONS: tuple[tuple[int, str], ...] = ((1, MIGRATION_1),)
