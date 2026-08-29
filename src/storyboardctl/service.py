from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from storyboardctl.database import Database
from storyboardctl.errors import Conflict, NotFound
from storyboardctl.models import AssetKind, ProjectSpec, ShotAssetSpec, ShotSpec
from storyboardctl.paths import file_sha256, normalize_relative_path, resolve_project_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StoryboardService:
    def __init__(self, database: Database, project_root: str | Path) -> None:
        self.database = database
        self.project_root = Path(project_root).resolve()

    def _existing_idempotent(
        self, connection: sqlite3.Connection, key: str | None
    ) -> dict[str, Any] | None:
        if key is None:
            return None
        row = connection.execute(
            "SELECT payload_json FROM events WHERE production_id = 1 AND idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        result = payload.get("result")
        return result if isinstance(result, dict) else None

    def _event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        result: dict[str, Any],
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(id, production_id, event_type, entity_type, entity_id, "
            "payload_json, idempotency_key, created_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (
                _id(),
                event_type,
                entity_type,
                entity_id,
                _json({"result": result}),
                idempotency_key,
                _now(),
            ),
        )

    def import_spec(
        self, spec: ProjectSpec, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            existing_production = connection.execute("SELECT id FROM production").fetchone()
            if existing_production is not None:
                replay = self._existing_idempotent(connection, idempotency_key)
                if replay is not None:
                    return replay
                raise Conflict("a production is already initialized in this database")

            created_at = _now()
            connection.execute(
                "INSERT INTO production(id, slug, title, root_path, metadata_json, created_at) "
                "VALUES (1, ?, ?, '.', ?, ?)",
                (spec.slug, spec.title, _json(spec.metadata), created_at),
            )
            version_id = _id()
            connection.execute(
                "INSERT INTO storyboard_versions(id, production_id, name, title, description, "
                "status, snapshot, created_at) VALUES (?, 1, ?, ?, ?, 'draft', 0, ?)",
                (
                    version_id,
                    spec.storyboard.name,
                    spec.storyboard.title,
                    spec.storyboard.description,
                    created_at,
                ),
            )
            for asset in spec.assets:
                connection.execute(
                    "INSERT INTO assets(id, production_id, asset_key, kind, path, title, "
                    "media_type, duration_seconds, metadata_json, created_at) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _id(),
                        asset.key,
                        asset.kind.value,
                        asset.path,
                        asset.title,
                        asset.media_type,
                        asset.duration_seconds,
                        _json(asset.metadata),
                        created_at,
                    ),
                )
            for cue in spec.music:
                asset_id = connection.execute(
                    "SELECT id FROM assets WHERE production_id = 1 AND asset_key = ?",
                    (cue.asset_key,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO music_cues(id, production_id, cue_key, asset_id, title, "
                    "metadata_json, created_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (_id(), cue.key, asset_id, cue.title, _json(cue.metadata), created_at),
                )

            shot_ids: dict[str, str] = {}
            positions: list[int] = []
            for index, shot in enumerate(spec.storyboard.shots, start=1):
                position = shot.position if shot.position is not None else index * 10
                shot_id = _id()
                shot_ids[shot.key] = shot_id
                connection.execute(
                    "INSERT INTO shots(id, production_id, shot_key, created_at) VALUES (?, 1, ?, ?)",
                    (shot_id, shot.key, created_at),
                )
                positions.append(position)
            for shot, position in zip(spec.storyboard.shots, positions, strict=True):
                revision_id, _ = self._insert_revision(connection, shot_ids[shot.key], shot)
                connection.execute(
                    "INSERT INTO version_shots(version_id, shot_id, revision_id, position, "
                    "created_at) VALUES (?, ?, ?, ?, ?)",
                    (version_id, shot_ids[shot.key], revision_id, position, created_at),
                )

            result = {
                "production": spec.slug,
                "version": spec.storyboard.name,
                "version_id": version_id,
                "snapshot": 0,
                "shots": len(spec.storyboard.shots),
            }
            self._event(
                connection,
                "project.imported",
                result,
                entity_type="storyboard_version",
                entity_id=version_id,
                idempotency_key=idempotency_key,
            )
            return result

    def _insert_revision(
        self, connection: sqlite3.Connection, shot_id: str, shot: ShotSpec
    ) -> tuple[str, int]:
        content = shot.model_dump(mode="json", exclude={"position"})
        content_hash = hashlib.sha256(_json(content).encode()).hexdigest()
        existing = connection.execute(
            "SELECT id, revision_number FROM shot_revisions WHERE shot_id = ? AND content_hash = ?",
            (shot_id, content_hash),
        ).fetchone()
        if existing is not None:
            return str(existing["id"]), int(existing["revision_number"])
        next_revision = int(
            connection.execute(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM shot_revisions WHERE shot_id = ?",
                (shot_id,),
            ).fetchone()[0]
        )
        revision_id = _id()
        connection.execute(
            "INSERT INTO shot_revisions(id, shot_id, revision_number, title, description, prompt, "
            "negative_prompt, duration_seconds, render_mode, seed, adapter, render_settings_json, "
            "notes_json, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                shot_id,
                next_revision,
                shot.title,
                shot.description,
                shot.prompt,
                shot.negative_prompt,
                shot.duration_seconds,
                shot.render_mode.value,
                shot.seed,
                shot.adapter,
                _json(shot.render_settings),
                _json(shot.notes),
                content_hash,
                _now(),
            ),
        )
        for linked in shot.assets:
            asset = connection.execute(
                "SELECT id FROM assets WHERE production_id = 1 AND asset_key = ?",
                (linked.asset_key,),
            ).fetchone()
            if asset is None:
                raise NotFound(f"asset not found: {linked.asset_key}")
            connection.execute(
                "INSERT INTO shot_assets(revision_id, asset_id, role, sort_order, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (revision_id, asset["id"], linked.role.value, linked.order, linked.notes),
            )
        for link in shot.links:
            target = connection.execute(
                "SELECT id FROM shots WHERE production_id = 1 AND shot_key = ?",
                (link.target_shot_key,),
            ).fetchone()
            if target is None:
                raise NotFound(f"target shot not found: {link.target_shot_key}")
            connection.execute(
                "INSERT INTO shot_links(revision_id, target_shot_id, kind, notes) VALUES (?, ?, ?, ?)",
                (revision_id, target["id"], link.kind.value, link.notes),
            )
        for cue_key, relationship in shot.music.items():
            cue = connection.execute(
                "SELECT id FROM music_cues WHERE production_id = 1 AND cue_key = ?",
                (cue_key,),
            ).fetchone()
            if cue is None:
                raise NotFound(f"music cue not found: {cue_key}")
            connection.execute(
                "INSERT INTO shot_music(revision_id, music_cue_id, relationship) VALUES (?, ?, ?)",
                (revision_id, cue["id"], relationship.value),
            )
        return revision_id, next_revision

    def list_versions(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, title, description, status, snapshot, parent_version_id "
                "FROM storyboard_versions ORDER BY created_at, name"
            ).fetchall()
        return [dict(row) for row in rows]

    def _version(
        self, connection: sqlite3.Connection, name: str, *, mutable: bool = False
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM storyboard_versions WHERE production_id = 1 AND name = ?", (name,)
        ).fetchone()
        if row is None:
            raise NotFound(f"storyboard version not found: {name}")
        if mutable and row["status"] != "draft":
            raise Conflict(f"storyboard {name!r} is {row['status']} and cannot be changed")
        return row

    @staticmethod
    def _check_snapshot(row: sqlite3.Row, expected: int | None) -> None:
        if expected is not None and int(row["snapshot"]) != expected:
            raise Conflict(
                f"storyboard snapshot conflict: expected {expected}, found {row['snapshot']}"
            )

    def list_shots(self, version_name: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            version = self._version(connection, version_name)
            archived_clause = "" if include_archived else "AND vs.archived_at IS NULL"
            rows = connection.execute(
                "SELECT s.id AS shot_id, s.shot_key, vs.position, vs.archived_at, "
                "sr.id AS revision_id, sr.revision_number, sr.title, sr.description, sr.prompt, "
                "sr.negative_prompt, sr.duration_seconds, sr.render_mode, sr.seed, sr.adapter, "
                "sr.render_settings_json, sr.notes_json, sr.content_hash "
                "FROM version_shots vs JOIN shots s ON s.id = vs.shot_id "
                "JOIN shot_revisions sr ON sr.id = vs.revision_id "
                f"WHERE vs.version_id = ? {archived_clause} ORDER BY vs.position",
                (version["id"],),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            result["render_settings"] = json.loads(result.pop("render_settings_json"))
            result["notes"] = json.loads(result.pop("notes_json"))
            results.append(result)
        return results

    def clone_storyboard(
        self, source_name: str, new_name: str, *, title: str | None = None
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            source = self._version(connection, source_name)
            if connection.execute(
                "SELECT 1 FROM storyboard_versions WHERE production_id = 1 AND name = ?", (new_name,)
            ).fetchone():
                raise Conflict(f"storyboard version already exists: {new_name}")
            version_id = _id()
            now = _now()
            connection.execute(
                "INSERT INTO storyboard_versions(id, production_id, name, title, description, "
                "status, snapshot, parent_version_id, created_at) "
                "VALUES (?, 1, ?, ?, ?, 'draft', 0, ?, ?)",
                (
                    version_id,
                    new_name,
                    title or str(source["title"]),
                    source["description"],
                    source["id"],
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO version_shots(version_id, shot_id, revision_id, position, archived_at, "
                "created_at, updated_at) SELECT ?, shot_id, revision_id, position, archived_at, ?, ? "
                "FROM version_shots WHERE version_id = ?",
                (version_id, now, now, source["id"]),
            )
            result = {"version": new_name, "version_id": version_id, "snapshot": 0}
            self._event(
                connection,
                "storyboard.cloned",
                result,
                entity_type="storyboard_version",
                entity_id=version_id,
            )
            return result

    def _shot_spec_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> ShotSpec:
        revision_id = row["revision_id"]
        assets = [
            {
                "asset_key": linked["asset_key"],
                "role": linked["role"],
                "order": linked["sort_order"],
                "notes": linked["notes"],
            }
            for linked in connection.execute(
                "SELECT a.asset_key, sa.role, sa.sort_order, sa.notes FROM shot_assets sa "
                "JOIN assets a ON a.id = sa.asset_id WHERE sa.revision_id = ? "
                "ORDER BY sa.role, sa.sort_order",
                (revision_id,),
            ).fetchall()
        ]
        links = [
            {
                "target_shot_key": linked["shot_key"],
                "kind": linked["kind"],
                "notes": linked["notes"],
            }
            for linked in connection.execute(
                "SELECT s.shot_key, sl.kind, sl.notes FROM shot_links sl "
                "JOIN shots s ON s.id = sl.target_shot_id WHERE sl.revision_id = ?",
                (revision_id,),
            ).fetchall()
        ]
        music = {
            linked["cue_key"]: linked["relationship"]
            for linked in connection.execute(
                "SELECT mc.cue_key, sm.relationship FROM shot_music sm JOIN music_cues mc "
                "ON mc.id = sm.music_cue_id WHERE sm.revision_id = ?",
                (revision_id,),
            ).fetchall()
        }
        return ShotSpec.model_validate(
            {
                "key": row["shot_key"],
                "position": row["position"],
                "title": row["title"],
                "description": row["description"],
                "prompt": row["prompt"],
                "negative_prompt": row["negative_prompt"],
                "duration_seconds": row["duration_seconds"],
                "render_mode": row["render_mode"],
                "seed": row["seed"],
                "adapter": row["adapter"],
                "render_settings": json.loads(row["render_settings_json"]),
                "notes": json.loads(row["notes_json"]),
                "assets": assets,
                "links": links,
                "music": music,
            }
        )

    def revise_shot(
        self,
        version_name: str,
        position: int,
        changes: dict[str, Any],
        *,
        expect_snapshot: int | None = None,
    ) -> dict[str, Any]:
        forbidden = {"key", "position", "assets", "links", "music"}.intersection(changes)
        if forbidden:
            raise Conflict(f"use dedicated relationship operations for: {sorted(forbidden)}")
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            self._check_snapshot(version, expect_snapshot)
            row = connection.execute(
                "SELECT s.shot_key, vs.*, sr.* FROM version_shots vs "
                "JOIN shots s ON s.id = vs.shot_id JOIN shot_revisions sr ON sr.id = vs.revision_id "
                "WHERE vs.version_id = ? AND vs.position = ? AND vs.archived_at IS NULL",
                (version["id"], position),
            ).fetchone()
            if row is None:
                raise NotFound(f"active shot not found at position {position}")
            current = self._shot_spec_from_row(connection, row)
            payload = current.model_dump(mode="json")
            payload.update(changes)
            revised = ShotSpec.model_validate(payload)
            revision_id, revision_number = self._insert_revision(connection, row["shot_id"], revised)
            now = _now()
            connection.execute(
                "UPDATE version_shots SET revision_id = ?, updated_at = ? "
                "WHERE version_id = ? AND shot_id = ?",
                (revision_id, now, version["id"], row["shot_id"]),
            )
            snapshot = int(version["snapshot"]) + 1
            connection.execute(
                "UPDATE storyboard_versions SET snapshot = ?, updated_at = ? WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {
                "version": version_name,
                "position": position,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "snapshot": snapshot,
            }
            self._event(connection, "shot.revised", result, entity_type="shot", entity_id=row["shot_id"])
            return result

    def add_shot(
        self,
        version_name: str,
        shot: ShotSpec,
        *,
        after_position: int | None = None,
        expect_snapshot: int | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            self._check_snapshot(version, expect_snapshot)
            active_positions = [
                int(row[0])
                for row in connection.execute(
                    "SELECT position FROM version_shots WHERE version_id = ? AND archived_at IS NULL "
                    "ORDER BY position",
                    (version["id"],),
                ).fetchall()
            ]
            if shot.position is not None:
                position = shot.position
            elif after_position is None:
                position = active_positions[-1] + 10 if active_positions else 10
            else:
                if after_position not in active_positions:
                    raise NotFound(f"active shot not found at position {after_position}")
                following = next((value for value in active_positions if value > after_position), None)
                if following is None:
                    position = after_position + 10
                elif following - after_position <= 1:
                    raise Conflict(
                        "no integer position remains between shots; run storyboard renumber --step 10"
                    )
                else:
                    position = (after_position + following) // 2
            if position in active_positions:
                raise Conflict(f"shot position is already in use: {position}")
            if connection.execute(
                "SELECT 1 FROM shots WHERE production_id = 1 AND shot_key = ?", (shot.key,)
            ).fetchone():
                raise Conflict(f"shot key already exists: {shot.key}")
            shot_id = _id()
            now = _now()
            connection.execute(
                "INSERT INTO shots(id, production_id, shot_key, created_at) VALUES (?, 1, ?, ?)",
                (shot_id, shot.key, now),
            )
            revision_id, revision_number = self._insert_revision(connection, shot_id, shot)
            connection.execute(
                "INSERT INTO version_shots(version_id, shot_id, revision_id, position, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (version["id"], shot_id, revision_id, position, now),
            )
            snapshot = int(version["snapshot"]) + 1
            connection.execute(
                "UPDATE storyboard_versions SET snapshot = ?, updated_at = ? WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {
                "version": version_name,
                "shot_id": shot_id,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "position": position,
                "snapshot": snapshot,
            }
            self._event(connection, "shot.added", result, entity_type="shot", entity_id=shot_id)
            return result

    def remove_shot(
        self,
        version_name: str,
        position: int,
        *,
        expect_snapshot: int | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            self._check_snapshot(version, expect_snapshot)
            row = connection.execute(
                "SELECT shot_id FROM version_shots WHERE version_id = ? AND position = ? "
                "AND archived_at IS NULL",
                (version["id"], position),
            ).fetchone()
            if row is None:
                raise NotFound(f"active shot not found at position {position}")
            now = _now()
            connection.execute(
                "UPDATE version_shots SET archived_at = ?, updated_at = ? "
                "WHERE version_id = ? AND shot_id = ?",
                (now, now, version["id"], row["shot_id"]),
            )
            snapshot = int(version["snapshot"]) + 1
            connection.execute(
                "UPDATE storyboard_versions SET snapshot = ?, updated_at = ? WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {"version": version_name, "position": position, "snapshot": snapshot}
            self._event(connection, "shot.removed", result, entity_type="shot", entity_id=row["shot_id"])
            return result

    def renumber_storyboard(
        self,
        version_name: str,
        *,
        step: int = 10,
        expect_snapshot: int | None = None,
    ) -> dict[str, Any]:
        if step < 1:
            raise Conflict("renumber step must be positive")
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            self._check_snapshot(version, expect_snapshot)
            rows = connection.execute(
                "SELECT shot_id FROM version_shots WHERE version_id = ? AND archived_at IS NULL "
                "ORDER BY position",
                (version["id"],),
            ).fetchall()
            now = _now()
            for index, row in enumerate(rows, start=1):
                connection.execute(
                    "UPDATE version_shots SET position = ?, updated_at = ? "
                    "WHERE version_id = ? AND shot_id = ?",
                    (1_000_000_000 + index, now, version["id"], row["shot_id"]),
                )
            for index, row in enumerate(rows, start=1):
                connection.execute(
                    "UPDATE version_shots SET position = ?, updated_at = ? "
                    "WHERE version_id = ? AND shot_id = ?",
                    (index * step, now, version["id"], row["shot_id"]),
                )
            snapshot = int(version["snapshot"]) + 1
            connection.execute(
                "UPDATE storyboard_versions SET snapshot = ?, updated_at = ? WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {
                "version": version_name,
                "step": step,
                "shots": len(rows),
                "snapshot": snapshot,
            }
            self._event(connection, "storyboard.renumbered", result, entity_id=version["id"])
            return result

    def lock_storyboard(self, version_name: str) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            snapshot = int(version["snapshot"]) + 1
            now = _now()
            connection.execute(
                "UPDATE storyboard_versions SET status = 'locked', snapshot = ?, updated_at = ? "
                "WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {"version": version_name, "status": "locked", "snapshot": snapshot}
            self._event(connection, "storyboard.locked", result, entity_id=version["id"])
            return result

    def add_asset(
        self,
        key: str,
        kind: AssetKind,
        path: str,
        *,
        title: str | None = None,
        media_type: str | None = None,
        duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        relative = normalize_relative_path(path)
        absolute = resolve_project_path(self.project_root, relative, must_exist=True)
        digest = file_sha256(absolute)
        with self.database.transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM assets WHERE production_id = 1 AND asset_key = ?", (key,)
            ).fetchone():
                raise Conflict(f"asset key already exists: {key}")
            asset_id = _id()
            connection.execute(
                "INSERT INTO assets(id, production_id, asset_key, kind, path, title, media_type, "
                "sha256, duration_seconds, created_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    key,
                    kind.value,
                    relative,
                    title,
                    media_type,
                    digest,
                    duration_seconds,
                    _now(),
                ),
            )
            result = {"asset_id": asset_id, "key": key, "path": relative, "sha256": digest}
            self._event(connection, "asset.added", result, entity_type="asset", entity_id=asset_id)
            return result

    def verify_asset(self, key: str) -> dict[str, Any]:
        from storyboardctl.errors import IntegrityFailure

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, path, sha256 FROM assets WHERE production_id = 1 AND asset_key = ? "
                "AND archived_at IS NULL",
                (key,),
            ).fetchone()
        if row is None:
            raise NotFound(f"asset not found: {key}")
        absolute = resolve_project_path(self.project_root, row["path"], must_exist=True)
        actual = file_sha256(absolute)
        if row["sha256"] and actual != row["sha256"]:
            raise IntegrityFailure(
                f"asset hash mismatch: {key}",
                details={"expected": row["sha256"], "actual": actual},
            )
        return {"asset_id": row["id"], "key": key, "verified": True, "sha256": actual}

    def _active_shot_row(
        self, connection: sqlite3.Connection, version_id: str, position: int
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT s.shot_key, vs.*, sr.* FROM version_shots vs "
            "JOIN shots s ON s.id = vs.shot_id JOIN shot_revisions sr ON sr.id = vs.revision_id "
            "WHERE vs.version_id = ? AND vs.position = ? AND vs.archived_at IS NULL",
            (version_id, position),
        ).fetchone()
        if row is None:
            raise NotFound(f"active shot not found at position {position}")
        return row

    def link_asset(
        self,
        version_name: str,
        position: int,
        asset_key: str,
        *,
        role: str = "reference",
        order: int = 0,
        notes: str | None = None,
        expect_snapshot: int | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name, mutable=True)
            self._check_snapshot(version, expect_snapshot)
            if connection.execute(
                "SELECT 1 FROM assets WHERE production_id = 1 AND asset_key = ? "
                "AND archived_at IS NULL",
                (asset_key,),
            ).fetchone() is None:
                raise NotFound(f"asset not found: {asset_key}")
            row = self._active_shot_row(connection, version["id"], position)
            current = self._shot_spec_from_row(connection, row)
            payload = current.model_dump(mode="json")
            payload["assets"].append(
                ShotAssetSpec(
                    asset_key=asset_key, role=role, order=order, notes=notes
                ).model_dump(mode="json")
            )
            revised = ShotSpec.model_validate(payload)
            revision_id, revision_number = self._insert_revision(connection, row["shot_id"], revised)
            now = _now()
            snapshot = int(version["snapshot"]) + 1
            connection.execute(
                "UPDATE version_shots SET revision_id = ?, updated_at = ? "
                "WHERE version_id = ? AND shot_id = ?",
                (revision_id, now, version["id"], row["shot_id"]),
            )
            connection.execute(
                "UPDATE storyboard_versions SET snapshot = ?, updated_at = ? WHERE id = ?",
                (snapshot, now, version["id"]),
            )
            result = {
                "version": version_name,
                "position": position,
                "asset_key": asset_key,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "snapshot": snapshot,
            }
            self._event(connection, "asset.linked", result, entity_type="shot", entity_id=row["shot_id"])
            return result

    def _plan_render_for_revision(
        self,
        connection: sqlite3.Connection,
        revision_id: str,
        *,
        path_label: str,
        position: int,
        seed: int,
        settings: dict[str, Any],
        source_render_id: str | None = None,
    ) -> dict[str, Any]:
        revision = connection.execute(
            "SELECT prompt FROM shot_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        if revision is None:
            raise NotFound(f"shot revision not found: {revision_id}")
        attempt = int(
            connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM renders WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()[0]
        )
        render_id = _id()
        fragment = render_id.split("-")[0]
        safe_label = "".join(char if char.isalnum() or char in "-_" else "-" for char in path_label)
        stem = f"shot_{position:04d}_{fragment}_a{attempt:03d}"
        output_path = f"renders/{safe_label}/{stem}.mp4"
        workflow_path = f"renders/{safe_label}/{stem}_workflow.json"
        connection.execute(
            "INSERT INTO renders(id, revision_id, attempt_number, state, seed, prompt_snapshot, "
            "settings_json, workflow_path, output_path, source_render_id, created_at) "
            "VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?)",
            (
                render_id,
                revision_id,
                attempt,
                seed,
                revision["prompt"],
                _json(settings),
                workflow_path,
                output_path,
                source_render_id,
                _now(),
            ),
        )
        return {
            "render_id": render_id,
            "revision_id": revision_id,
            "attempt_number": attempt,
            "state": "planned",
            "seed": seed,
            "settings": settings,
            "workflow_path": workflow_path,
            "output_path": output_path,
            "source_render_id": source_render_id,
        }

    def plan_render(
        self,
        version_name: str,
        position: int,
        *,
        seed: int | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            version = self._version(connection, version_name)
            row = self._active_shot_row(connection, version["id"], position)
            merged_settings = json.loads(row["render_settings_json"])
            if settings:
                merged_settings.update(settings)
            resolved_seed = seed if seed is not None else row["seed"]
            if resolved_seed is None:
                resolved_seed = secrets.randbits(63)
            result = self._plan_render_for_revision(
                connection,
                row["revision_id"],
                path_label=version_name,
                position=position,
                seed=int(resolved_seed),
                settings=merged_settings,
            )
            self._event(
                connection,
                "render.planned",
                result,
                entity_type="render",
                entity_id=result["render_id"],
            )
            return result

    def _render_row(self, connection: sqlite3.Connection, render_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
        if row is None:
            raise NotFound(f"render not found: {render_id}")
        return row

    def retry_render(self, render_id: str) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            source = self._render_row(connection, render_id)
            result = self._plan_render_for_revision(
                connection,
                source["revision_id"],
                path_label="retry",
                position=int(source["attempt_number"]),
                seed=int(source["seed"]),
                settings=json.loads(source["settings_json"]),
                source_render_id=render_id,
            )
            self._event(connection, "render.retried", result, entity_type="render", entity_id=result["render_id"])
            return result

    def rerender(self, render_id: str, *, seed: int | None = None) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            source = self._render_row(connection, render_id)
            result = self._plan_render_for_revision(
                connection,
                source["revision_id"],
                path_label="rerender",
                position=int(source["attempt_number"]),
                seed=seed if seed is not None else secrets.randbits(63),
                settings=json.loads(source["settings_json"]),
                source_render_id=render_id,
            )
            self._event(connection, "render.rerendered", result, entity_type="render", entity_id=result["render_id"])
            return result

    def transition_render(
        self,
        render_id: str,
        state: str,
        *,
        comfy_prompt_id: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "planned": {"queued", "failed", "cancelled"},
            "queued": {"running", "completed", "failed", "cancelled"},
            "running": {"completed", "failed", "cancelled"},
            "completed": set(),
            "failed": set(),
            "cancelled": set(),
        }
        if state not in allowed:
            raise Conflict(f"unknown render state: {state}")
        with self.database.transaction(write=True) as connection:
            row = self._render_row(connection, render_id)
            if state not in allowed[str(row["state"])]:
                raise Conflict(f"invalid render transition: {row['state']} -> {state}")
            now = _now()
            fields = ["state = ?", "error_message = ?"]
            values: list[Any] = [state, error_message]
            if comfy_prompt_id is not None:
                fields.append("comfy_prompt_id = ?")
                values.append(comfy_prompt_id)
            timestamp_field = {"queued": "queued_at", "running": "started_at", "completed": "completed_at"}.get(state)
            if timestamp_field:
                fields.append(f"{timestamp_field} = ?")
                values.append(now)
            values.append(render_id)
            connection.execute(f"UPDATE renders SET {', '.join(fields)} WHERE id = ?", values)
            result = {"render_id": render_id, "state": state, "comfy_prompt_id": comfy_prompt_id or row["comfy_prompt_id"]}
            self._event(connection, "render.transitioned", result, entity_type="render", entity_id=render_id)
            return result

    def complete_render(self, render_id: str, *, duration_seconds: float) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._render_row(connection, render_id)
        if row["state"] not in ("queued", "running"):
            raise Conflict(f"invalid render transition: {row['state']} -> completed")
        output = resolve_project_path(self.project_root, row["output_path"], must_exist=True)
        digest = file_sha256(output)
        result = self.transition_render(render_id, "completed")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE renders SET output_sha256 = ?, duration_seconds = ? WHERE id = ?",
                (digest, duration_seconds, render_id),
            )
        result.update({"output_path": row["output_path"], "output_sha256": digest, "duration_seconds": duration_seconds})
        return result

    def _review_render(
        self, render_id: str, decision: str, *, reviewer: str | None, notes: str | None
    ) -> dict[str, Any]:
        with self.database.transaction(write=True) as connection:
            render = self._render_row(connection, render_id)
            if render["state"] != "completed":
                raise Conflict("only completed renders can be reviewed")
            review_id = _id()
            now = _now()
            connection.execute(
                "INSERT INTO render_reviews(id, render_id, decision, reviewer, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (review_id, render_id, decision, reviewer, notes, now),
            )
            if decision == "approved":
                connection.execute(
                    "INSERT INTO approved_renders(revision_id, render_id, review_id, selected_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(revision_id) DO UPDATE SET "
                    "render_id = excluded.render_id, review_id = excluded.review_id, "
                    "selected_at = excluded.selected_at",
                    (render["revision_id"], render_id, review_id, now),
                )
            else:
                connection.execute("DELETE FROM approved_renders WHERE render_id = ?", (render_id,))
            result = {"review_id": review_id, "render_id": render_id, "decision": decision}
            self._event(connection, f"render.{decision}", result, entity_type="render", entity_id=render_id)
            return result

    def approve_render(
        self, render_id: str, *, reviewer: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        return self._review_render(render_id, "approved", reviewer=reviewer, notes=notes)

    def reject_render(
        self, render_id: str, *, reviewer: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        return self._review_render(render_id, "rejected", reviewer=reviewer, notes=notes)

    def approved_render(self, version_name: str, position: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            version = self._version(connection, version_name)
            row = self._active_shot_row(connection, version["id"], position)
            approved = connection.execute(
                "SELECT r.* FROM approved_renders ar JOIN renders r ON r.id = ar.render_id "
                "WHERE ar.revision_id = ?",
                (row["revision_id"],),
            ).fetchone()
        if approved is None:
            return None
        result = dict(approved)
        result["render_id"] = result.pop("id")
        result["settings"] = json.loads(result.pop("settings_json"))
        return result

    def review_history(self, render_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            self._render_row(connection, render_id)
            rows = connection.execute(
                "SELECT id AS review_id, render_id, decision, reviewer, notes, created_at "
                "FROM render_reviews WHERE render_id = ? ORDER BY created_at, id",
                (render_id,),
            ).fetchall()
        return [dict(row) for row in rows]
