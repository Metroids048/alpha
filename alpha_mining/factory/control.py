"""Persistent fail-closed controls shared by every production entry point."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.storage.migrations import migrate


@dataclass(frozen=True)
class FactoryState:
    hard_stop: bool
    reason: str
    updated_at: str
    ledger_sync_id: str
    cluster_freeze_complete: bool
    execute_submit: bool
    execute_description_patch: bool


class FactoryControl:
    def __init__(self, database: str | Path = "research_memory.sqlite") -> None:
        self.database = Path(database)
        migrate(self.database)

    def status(self) -> FactoryState:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT hard_stop,reason,updated_at,ledger_sync_id,cluster_freeze_complete,execute_submit,execute_description_patch FROM factory_control WHERE singleton=1"
            ).fetchone()
        if row is None:
            return FactoryState(True, "control_state_missing", "", "", False, False, False)
        return FactoryState(
            bool(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]),
            bool(row[5]), bool(row[6])
        )

    def stop(self, reason: str) -> FactoryState:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE factory_control SET hard_stop=1,reason=?,execute_submit=0,updated_at=? WHERE singleton=1",
                (str(reason or "manual_stop"), now),
            )
        return self.status()

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
