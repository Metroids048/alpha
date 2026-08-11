"""Read-only search for the B evidence across local SQLite files."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = {
    "78zwRNqx", "e73OZE9z", "2rp7V1wb", "JjGp65nA", "ak1d5dlv",
    "09e0656633e48fb0", "75f9ff9af8bdabae", "e7b26c4dbdcc5ec4",
    "6f969e8c41e2d1d3", "dab7a3c591646ca4",
}


def main() -> int:
    files = sorted({*ROOT.glob("*.sqlite"), *ROOT.glob("*.sqlite3"), *ROOT.glob("数据/**/*.sqlite"), *ROOT.glob("数据/**/*.sqlite3")})
    results = []
    for path in files:
        try:
            uri = f"file:{path.resolve().as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as con:
                tables = [str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                hits = []
                for table in tables:
                    columns = [str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")')]
                    searchable = [column for column in columns if column.lower() in {"alpha_id", "candidate_id", "expression", "provenance", "settings_json", "payload_json", "metrics_json"}]
                    if not searchable:
                        continue
                    quoted = ",".join('"' + column.replace('"', '""') + '"' for column in searchable)
                    try:
                        rows = con.execute(f'SELECT {quoted} FROM "{table}" LIMIT 50000').fetchall()
                    except sqlite3.DatabaseError:
                        continue
                    for row in rows:
                        rendered = " | ".join(str(value or "") for value in row)
                        if any(target in rendered for target in TARGETS):
                            hits.append({"table": table, "columns": searchable, "row": row})
                if hits:
                    results.append({"path": str(path), "hits": hits[:50]})
        except (OSError, sqlite3.DatabaseError):
            continue
    print(json.dumps({"files_scanned": [str(path) for path in files], "matches": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
