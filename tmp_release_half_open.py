"""Release a crash-orphaned HALF_OPEN lock on the AUTHORITATIVE database.

A recovery probe was in flight when Chrome died, so record_response never ran.
access.py:211 rejects every request while HALF_OPEN and has no timeout, so this
never self-heals.  Explicitly authorised orphan-lock release -- NOT a rate-limit
bypass: alpha_list's own window expired at 2026-08-11T09:42Z.

Deliberately unlike tools/ops/refresh_catalog_and_reset.py:49-58:
  * targets 数据/本地运行产物/数据库/ , never the stale repo-root file
  * PRESERVES recovery_attempts (does not wash the recovery budget)
  * reason = 'orphan_half_open_released'
  * refuses to run unless the probe is provably dead
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = (ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite").resolve()
STALE = (ROOT / "research_memory.sqlite").resolve()


def parse_iso(text: object) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    now = datetime.now(timezone.utc)

    # ── prove we are on the authoritative database ───────────────────────────
    print("=== DATABASE IDENTITY ===")
    print(f"EFFECTIVE_DATABASE = {DB}")
    if not DB.is_file():
        print("ABORT: authoritative database not found")
        return 1
    size = DB.stat().st_size
    print(f"SIZE               = {size:,} bytes ({size / 1048576:.1f} MiB)")
    print(f"MTIME              = {datetime.fromtimestamp(DB.stat().st_mtime, timezone.utc).isoformat()}")
    print(f"NOW_UTC            = {now.isoformat()}")
    print(f"(stale repo-root file {STALE} is NOT touched)")
    if DB == STALE:
        print("ABORT: refusing to operate on the repo-root file")
        return 1

    with sqlite3.connect(DB) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state,reason,opened_at,retry_after_until,recovery_attempts,max_auto_recoveries "
            "FROM platform_access_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            print("ABORT: no platform_access_state row")
            return 1
        state, reason, opened_at, until_text, attempts, maximum = (
            str(row[0]), str(row[1] or ""), row[2], row[3], int(row[4]), int(row[5])
        )
        print("\n=== BEFORE ===")
        print(f"state             = {state}")
        print(f"reason            = {reason!r}")
        print(f"opened_at         = {opened_at}")
        print(f"retry_after_until = {until_text or '(null)'}")
        print(f"recovery_attempts = {attempts}/{maximum}")

        if state != "HALF_OPEN":
            print(f"\nNOTHING_TO_DO: state is {state}, not HALF_OPEN — continuing with actual state.")
            return 0

        # ── precondition: the orphaning probe must be provably dead ──────────
        last = con.execute(
            "SELECT timestamp,endpoint_class,method,status_code FROM platform_request_events "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if last is None:
            print("\nABORT: no request events to corroborate an orphaned probe")
            return 1
        ts, endpoint, method, code = str(last[0]), str(last[1]), str(last[2]), int(last[3])
        print("\n=== PRECONDITIONS ===")
        print(f"latest event      = {ts}  {endpoint} {method} status={code}")

        checks: list[tuple[str, bool, str]] = []
        checks.append(("state == HALF_OPEN", True, state))
        checks.append((
            "latest event status_code == 0 (transport died, no response recorded)",
            code == 0, f"status={code}",
        ))
        until = parse_iso(until_text)
        expired = until is not None and now >= until
        checks.append((
            "retry_after_until has expired",
            expired,
            f"{until_text} vs now {now.isoformat()}",
        ))
        # No later 2xx/429 may exist after the orphaned probe: that would prove
        # the probe actually completed and the lock is live, not orphaned.
        newer = con.execute(
            "SELECT COUNT(*) FROM platform_request_events "
            "WHERE timestamp > ? AND status_code != 0",
            (ts,),
        ).fetchone()[0]
        checks.append((
            "no answered response after the orphaned probe",
            int(newer) == 0, f"{newer} newer answered events",
        ))

        for label, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}  ({detail})")
        if not all(ok for _, ok, _ in checks):
            print("\nABORT: preconditions not met — the lock may be live, refusing to release.")
            return 1

        # ── release: keep recovery_attempts, do not touch history ───────────
        stamp = now.isoformat().replace("+00:00", "Z")
        con.execute(
            "UPDATE platform_access_state SET state='CLOSED',"
            "reason='orphan_half_open_released',retry_after_until=NULL,updated_at=? "
            "WHERE singleton=1",
            (stamp,),
        )

    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as con:
        after = con.execute(
            "SELECT state,reason,retry_after_until,recovery_attempts,max_auto_recoveries "
            "FROM platform_access_state WHERE singleton=1"
        ).fetchone()
        events = con.execute("SELECT COUNT(*) FROM platform_request_events").fetchone()[0]

    print("\n=== AFTER ===")
    print(f"state             = {after[0]}")
    print(f"reason            = {after[1]!r}")
    print(f"retry_after_until = {after[2] or '(null)'}")
    print(f"recovery_attempts = {after[3]}/{after[4]}   (preserved, NOT reset)")
    print(f"request_events    = {events} rows (history untouched)")
    print("\nRELEASED: orphaned HALF_OPEN cleared. No quota bypassed, no history rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
