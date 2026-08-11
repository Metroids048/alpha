"""Read-only diagnostic for locating platform provenance in the effective DB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
TARGETS = {
    "78zwRNqx",
    "e73OZE9z",
    "2rp7V1wb",
    "JjGp65nA",
    "ak1d5dlv",
    "09e0656633e48fb0",
    "75f9ff9af8bdabae",
    "e7b26c4dbdcc5ec4",
    "6f969e8c41e2d1d3",
    "dab7a3c591646ca4",
}


def main() -> int:
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        tables = [str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(json.dumps({"db": str(DB), "tables": tables}, ensure_ascii=False, indent=2))
        for table in tables:
            columns = [str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")')]
            searchable = [column for column in columns if column.lower() in {"alpha_id", "candidate_id", "request_hash", "provenance", "expression", "settings_json", "payload_json", "metrics_json"}]
            if table in {"candidate_outcomes", "simulation_requests", "legacy_alphas", "platform_alpha_ledger", "platform_alpha_observations", "simulation_runs"}:
                print(json.dumps({"table": table, "columns": columns}, ensure_ascii=False))
            if not searchable:
                continue
            query = f'SELECT {",".join("""""" + column.replace("""""", """""""""""""" ) + """""" for column in searchable)} FROM "{table}" LIMIT 20000'
            try:
                rows = con.execute(query).fetchall()
            except sqlite3.DatabaseError as exc:
                print(json.dumps({"table": table, "error": str(exc)}, ensure_ascii=False))
                continue
            hits = []
            for row in rows:
                rendered = " | ".join(str(value or "") for value in row)
                if any(target in rendered for target in TARGETS):
                    hits.append(dict(zip(searchable, row)))
            if hits:
                print(json.dumps({"table": table, "columns": searchable, "hits": hits[:20]}, ensure_ascii=False, indent=2))
            if "provenance" in {column.lower() for column in columns}:
                try:
                    counts = con.execute(f'SELECT provenance,COUNT(*) FROM "{table}" GROUP BY provenance').fetchall()
                    print(json.dumps({"table": table, "provenance_counts": counts}, ensure_ascii=False))
                    if table == "candidate_outcomes":
                        verified_rows = con.execute(
                            'SELECT candidate_id,expression,outcome,quality_status,sharpe,fitness,turnover,provenance FROM candidate_outcomes WHERE provenance="PLATFORM_VERIFIED" ORDER BY observed_at DESC LIMIT 10'
                        ).fetchall()
                        print(json.dumps({"table": table, "verified_rows": verified_rows}, ensure_ascii=False, indent=2))
                except sqlite3.DatabaseError:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
