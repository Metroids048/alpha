"""READ-ONLY: compare breaker state across both sqlite files.

The repo root research_memory.sqlite is a stale 2026-08-06 leftover; the
authoritative one lives under 数据/本地运行产物/数据库/.  Reading the wrong one
reports a bogus CLOSED.  Prints both, plus the events each one last recorded.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUTHORITATIVE = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
STALE = ROOT / "research_memory.sqlite"


def dump(label: str, db: Path) -> None:
    print(f"\n=== {label} ===")
    print(f"path   : {db}")
    print(f"exists : {db.exists()}")
    if not db.exists():
        return
    stat = db.stat()
    print(f"size   : {stat.st_size / 1024:.0f} KiB")
    print(f"mtime  : {datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}")
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    row = con.execute(
        "SELECT state,reason,opened_at,retry_after_until,recovery_attempts,"
        "max_auto_recoveries,updated_at FROM platform_access_state WHERE singleton=1"
    ).fetchone()
    if row is None:
        print("platform_access_state: (no singleton row)")
        return
    state, reason, opened, until, attempts, maximum, updated = row
    print(f"state             : {state}")
    print(f"reason            : {reason}")
    print(f"opened_at         : {opened}")
    print(f"retry_after_until : {until or '(null)'}")
    print(f"recovery_attempts : {attempts}/{maximum}")
    print(f"updated_at        : {updated}")
    last = con.execute(
        "SELECT timestamp,endpoint_class,method,status_code FROM platform_request_events "
        "ORDER BY timestamp DESC LIMIT 3"
    ).fetchall()
    print("last events:")
    for ts, endpoint, method, code in last:
        print(f"  {ts}  {endpoint:18s} {method:4s} {code}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"NOW_UTC = {datetime.now(timezone.utc).isoformat()}")
    dump("AUTHORITATIVE (what the pipeline actually uses)", AUTHORITATIVE)
    dump("STALE repo-root leftover (what tmp_check_breaker.py wrongly read)", STALE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
