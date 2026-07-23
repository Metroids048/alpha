"""Persistent fail-closed controls shared by every production entry point."""

from __future__ import annotations

import sqlite3
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.storage.migrations import MIGRATIONS, backup_and_migrate, migrate


def _sanitize_diagnostic(value: str) -> str:
    return re.sub(
        r"(?i)\b(password|passwd|token|cookie|authorization)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        str(value or ""),
    )


@dataclass(frozen=True)
class FactoryState:
    hard_stop: bool
    reason: str
    updated_at: str
    ledger_sync_id: str
    cluster_freeze_complete: bool
    execute_submit: bool
    execute_description_patch: bool
    stop_kind: str
    readiness_state: str
    readiness_reason: str


class FactoryControl:
    def __init__(self, database: str | Path = "research_memory.sqlite") -> None:
        self.database = Path(database)
        needs_migration = self.database.is_file()
        if needs_migration:
            try:
                with sqlite3.connect(self.database) as con:
                    applied = {
                        int(row[0])
                        for row in con.execute("SELECT version FROM schema_migrations")
                    }
                needs_migration = any(version not in applied for version, _sql in MIGRATIONS)
            except sqlite3.DatabaseError:
                needs_migration = True
        if needs_migration:
            backup_and_migrate(self.database)
        else:
            migrate(self.database)

    def status(self) -> FactoryState:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT hard_stop,reason,updated_at,ledger_sync_id,cluster_freeze_complete,execute_submit,execute_description_patch,stop_kind,readiness_state,readiness_reason FROM factory_control WHERE singleton=1"
            ).fetchone()
        if row is None:
            return FactoryState(True, "control_state_missing", "", "", False, False, False, "security", "", "control_state_missing")
        return FactoryState(
            bool(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]),
            bool(row[5]), bool(row[6]), str(row[7] or ""), str(row[8] or ""), str(row[9] or "")
        )

    def stop(self, reason: str, *, stop_kind: str = "manual") -> FactoryState:
        kind = str(stop_kind or "").strip().lower()
        if kind not in {"manual", "security", "data_integrity"}:
            raise ValueError("stop_kind must be manual, security, or data_integrity")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE factory_control SET hard_stop=1,stop_kind=?,reason=?,readiness_state='',readiness_reason='',execute_submit=0,updated_at=? WHERE singleton=1",
                (kind, str(reason or "manual_stop"), now),
            )
        return self.status()

    def release(self, confirmation: str, *, reason: str = "manual_release") -> FactoryState:
        """Clear hard_stop after ledger + cluster freeze; never enables submit/patch writes."""
        if confirmation != "RELEASE_FACTORY_HARD_STOP":
            raise PermissionError("factory release confirmation is invalid")
        state = self.status()
        if not state.ledger_sync_id:
            raise PermissionError("COMPLETE ledger_sync_id is required before release")
        if not state.cluster_freeze_complete:
            raise PermissionError("cluster_freeze_complete is required before release")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE factory_control SET hard_stop=0,stop_kind='',reason=?,updated_at=? WHERE singleton=1",
                (str(reason or "manual_release"), now),
            )
        return self.status()

    def record_cycle_outcome(
        self,
        *,
        cycle: int,
        category: str,
        rc: int,
        consecutive_failures: int,
        task_id: str = "",
        input_id: str = "",
        retry_after_seconds: float | None = None,
        detail: str = "",
        traceback_text: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            if str(category) != "SUCCESS":
                con.execute(
                    """INSERT INTO loop_incidents
                    (cycle,task_id,input_id,category,rc,consecutive_cycle_failures,
                     retry_after_seconds,detail,traceback_text,occurred_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(cycle), str(task_id or ""), str(input_id or ""), str(category),
                        int(rc), int(consecutive_failures), retry_after_seconds,
                        _sanitize_diagnostic(detail), _sanitize_diagnostic(traceback_text), now,
                    ),
                )
            con.execute(
                """UPDATE loop_health SET current_cycle=?,consecutive_cycle_failures=?,
                   last_success_at=CASE WHEN ?='SUCCESS' THEN ? ELSE last_success_at END,
                   last_failure_at=CASE WHEN ?<>'SUCCESS' THEN ? ELSE last_failure_at END,
                   last_failure_category=CASE WHEN ?<>'SUCCESS' THEN ? ELSE last_failure_category END,
                   last_exception=CASE WHEN ?<>'SUCCESS' THEN ? ELSE last_exception END,
                   recovery_attempts=recovery_attempts+CASE WHEN ?<>'SUCCESS' THEN 1 ELSE 0 END,
                   updated_at=? WHERE singleton=1""",
                (
                    int(cycle), int(consecutive_failures), str(category), now,
                    str(category), now, str(category), str(category),
                    str(category), _sanitize_diagnostic(detail), str(category), now,
                ),
            )

    def can_generate(self) -> bool:
        state = self.status()
        return not state.hard_stop and bool(state.ledger_sync_id) and state.cluster_freeze_complete

    def can_submit(self) -> bool:
        state = self.status()
        return self.can_generate() and state.execute_submit

    def can_patch_description(self) -> bool:
        state = self.status()
        return self.can_generate() and state.execute_description_patch

    def set_write_access(
        self,
        *,
        patch: bool,
        submit: bool,
        confirmation: str,
    ) -> FactoryState:
        if confirmation != "I_UNDERSTAND_PLATFORM_WRITES":
            raise PermissionError("platform write confirmation is invalid")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE factory_control SET execute_description_patch=?,execute_submit=?,
                   updated_at=? WHERE singleton=1""",
                (int(bool(patch)), int(bool(submit)), now),
            )
        return self.status()
