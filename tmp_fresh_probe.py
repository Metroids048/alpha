"""Read-only snapshot of the authoritative candidate queue (temp harness)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"

PREFERRED = (
    "candidate_id",
    "id",
    "expression",
    "queue_status",
    "state",
    "status",
    "created_at",
    "source",
    "degraded",
    "dataset_id",
    "local_quality_score",
    "novelty_score",
    "self_corr_risk_score",
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None, help="only rows with created_at >= this")
    parser.add_argument("--status", default="PENDING_SIMULATION")
    args = parser.parse_args()

    print(f"DB={DB} exists={DB.exists()}")
    if not DB.exists():
        return 1
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        tables = [str(r[0]) for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        for table in tables:
            cols = [str(r[1]) for r in con.execute(f'PRAGMA table_info("{table}")')]
            if "queue_status" not in cols:
                continue
            print(f"\n=== {table} ===")
            print("columns:", ",".join(cols))
            for status, count in con.execute(
                f'SELECT queue_status, COUNT(*) FROM "{table}" GROUP BY queue_status ORDER BY 2 DESC'
            ):
                print(f"  {status}: {count}")
            picked = [c for c in PREFERRED if c in cols]
            where = ["queue_status = ?"]
            params: list[object] = [args.status]
            if args.since and "created_at" in cols:
                where.append("created_at >= ?")
                params.append(args.since)
            query = (
                f'SELECT {",".join(chr(34) + c + chr(34) for c in picked)} FROM "{table}" '
                f'WHERE {" AND ".join(where)} ORDER BY '
                + ("created_at" if "created_at" in cols else picked[0])
            )
            rows = con.execute(query, params).fetchall()
            print(f"rows matching status={args.status} since={args.since}: {len(rows)}")
            for row in rows:
                print(json.dumps(dict(zip(picked, row)), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
