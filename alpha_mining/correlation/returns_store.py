"""SQLite daily returns storage with date-aligned reads."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class ReturnsStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def put(
        self,
        expression_id: str,
        returns: list[tuple[str, float]],
        *,
        alpha_id: str = "",
        source: str = "",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.database) as con:
            before = con.total_changes
            con.executemany(
                "INSERT OR REPLACE INTO alpha_daily_returns(expression_id,alpha_id,date,daily_return,source,created_at) VALUES (?,?,?,?,?,?)",
                [
                    (expression_id, alpha_id, date, float(value), source, now)
                    for date, value in returns
                ],
            )
            return con.total_changes - before

    def get(self, expression_id: str) -> list[tuple[str, float]]:
        with sqlite3.connect(self.database) as con:
            return [
                (str(date), float(value))
                for date, value in con.execute(
                    "SELECT date,daily_return FROM alpha_daily_returns WHERE expression_id=? ORDER BY date",
                    (expression_id,),
                )
            ]
