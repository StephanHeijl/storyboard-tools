from __future__ import annotations

import sqlite3

import pytest

from storyboardctl.database import Database


def test_initialize_is_idempotent_and_enables_safety_pragmas(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    assert [row[0] for row in migrations] == [1, 2, 3]
    for required in (
        "production",
        "storyboard_versions",
        "shots",
        "shot_revisions",
        "version_shots",
        "assets",
        "renders",
        "render_reviews",
        "approved_renders",
        "compilations",
        "quality_reports",
        "compilation_reviews",
        "approved_compilations",
        "render_finalization_claims",
        "events",
    ):
        assert required in tables


def test_write_transaction_rolls_back_on_error(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()

    with pytest.raises(RuntimeError), database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO production(id, slug, title, root_path, created_at) "
            "VALUES (1, 'rolled-back', 'Rolled back', '.', 'now')"
        )
        raise RuntimeError("stop")

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM production").fetchone()[0] == 0


def test_foreign_keys_and_event_idempotency_are_enforced(tmp_path) -> None:
    database = Database(tmp_path / "storyboard.db")
    database.initialize()
    with database.transaction(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO storyboard_versions(id, production_id, name, title, status, "
                "snapshot, created_at) VALUES ('v', 99, 'v1', 'V1', 'draft', 0, 'now')"
            )
        connection.execute(
            "INSERT INTO production(id, slug, title, root_path, created_at) VALUES (1, 'sample', 'Sample', '.', 'now')"
        )
        connection.execute(
            "INSERT INTO events(id, production_id, event_type, payload_json, idempotency_key, "
            "created_at) VALUES ('e1', 1, 'sample', '{}', 'same-key', 'now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO events(id, production_id, event_type, payload_json, "
                "idempotency_key, created_at) VALUES ('e2', 1, 'sample', '{}', "
                "'same-key', 'now')"
            )
