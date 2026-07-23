from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_known_readiness_hard_stop_is_migrated_to_recoverable_state(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "control.sqlite"
    with sqlite3.connect(database) as con:
        con.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE factory_control(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), hard_stop INTEGER NOT NULL,
                reason TEXT NOT NULL, updated_at TEXT NOT NULL, ledger_sync_id TEXT NOT NULL DEFAULT '',
                cluster_freeze_complete INTEGER NOT NULL DEFAULT 0, execute_submit INTEGER NOT NULL DEFAULT 0,
                execute_description_patch INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE platform_access_state(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '', retry_after_until TEXT
            );
            INSERT INTO platform_access_state(singleton,state) VALUES(1,'CLOSED');
            INSERT INTO factory_control(singleton,hard_stop,reason,updated_at)
            VALUES(1,1,'cluster_freeze_required','2026-01-01');
            """
        )
        con.executemany(
            "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
            [(version, "2026-01-01") for version in range(1, 11)],
        )
        con.commit()
    migrate(database)
    from alpha_mining.factory.control import FactoryControl

    state = FactoryControl(database).status()
    assert state.hard_stop is False
    assert state.stop_kind == ""
    assert state.readiness_state == "cluster_freeze_required"


def test_only_explicit_stop_kinds_can_create_hard_stop(tmp_path: Path) -> None:
    from alpha_mining.factory.control import FactoryControl

    control = FactoryControl(tmp_path / "control.sqlite")
    with pytest.raises(ValueError, match="stop_kind"):
        control.stop("http_429", stop_kind="rate_limited")

    stopped = control.stop("operator requested stop", stop_kind="manual")
    assert stopped.hard_stop is True
    assert stopped.stop_kind == "manual"


def test_loop_incident_and_health_tables_are_available(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "health.sqlite"
    migrate(database)
    with sqlite3.connect(database) as con:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1] for row in con.execute("PRAGMA table_info(loop_incidents)")
        }
    assert {"loop_health", "loop_incidents"} <= tables
    assert {"cycle", "task_id", "input_id", "category", "traceback_text"} <= columns


def test_factory_control_backs_up_existing_database_before_migration(
    tmp_path: Path, monkeypatch
) -> None:
    import alpha_mining.storage.migrations as migrations

    database = tmp_path / "legacy.sqlite"
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:10])
    migrations.migrate(database)
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)

    from alpha_mining.factory.control import FactoryControl

    FactoryControl(database)

    backups = list(tmp_path.glob("legacy.sqlite.backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cycle_incident_storage_redacts_secret_like_diagnostics(tmp_path: Path) -> None:
    from alpha_mining.factory.control import FactoryControl

    database = tmp_path / "incident.sqlite"
    control = FactoryControl(database)
    control.record_cycle_outcome(
        cycle=9,
        category="UNKNOWN_RUNTIME_ERROR",
        rc=7,
        consecutive_failures=2,
        detail="password=should-not-persist",
        traceback_text="Traceback: token=also-secret",
    )
    with sqlite3.connect(database) as con:
        detail, traceback_text = con.execute(
            "SELECT detail,traceback_text FROM loop_incidents"
        ).fetchone()
    assert "should-not-persist" not in detail
    assert "also-secret" not in traceback_text
