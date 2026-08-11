"""Read-only monitor for the temporary quality-pilot SQLite ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path


PATH = Path(__file__).resolve().parent / "tmp_alpha_quality_pilot_workspace_fresh" / "pilot.sqlite"


def main() -> int:
    with sqlite3.connect(f"file:{PATH.as_posix()}?mode=ro", uri=True) as con:
        rows = con.execute(
            "SELECT round,ordinal,alpha_id,status,quality_status,error FROM simulations ORDER BY rowid"
        ).fetchall()
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
