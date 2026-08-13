"""READ-ONLY rate-limit gate for the single alpha_list request.

Prints the authoritative alpha_list rate-limit facts and decides whether the
one permitted alpha_list GET may be sent.  No network, no writes.
Exit 0 = clear to send.  Exit 4 = still inside the platform's retry window.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"


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
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)

    row = con.execute(
        "SELECT timestamp, status_code, retry_after_seconds, retry_after_until "
        "FROM platform_request_events WHERE endpoint_class='alpha_list' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    if row is None:
        print("LAST_ALPHA_LIST_AT   = (none)")
        print("LAST_STATUS          = (none)")
        print("LAST_RETRY_AFTER     = (none)")
        print("RETRY_AFTER_UNTIL    = (none)")
        print(f"CURRENT_TIME_UTC     = {now.isoformat()}")
        print("\nDECISION: CLEAR_TO_SEND (no alpha_list history)")
        return 0

    last_at, status, retry_after, retry_until = row
    print(f"LAST_ALPHA_LIST_AT   = {last_at}")
    print(f"LAST_STATUS          = {status}")
    print(f"LAST_RETRY_AFTER     = {retry_after}")
    print(f"RETRY_AFTER_UNTIL    = {retry_until or '(null)'}")
    print(f"CURRENT_TIME_UTC     = {now.isoformat()}")

    # The authoritative wait is the recorded retry_after_until.  When the column
    # is null, reconstruct it from the event timestamp + Retry-After header.
    until = parse_iso(retry_until)
    source = "retry_after_until column"
    if until is None:
        event_at = parse_iso(last_at)
        seconds = float(retry_after or 0.0)
        if event_at is not None and seconds > 0:
            until = event_at + timedelta(seconds=seconds)
            source = f"{last_at} + Retry-After {seconds:g}s"

    print("\n=== recent alpha_list history ===")
    for ts, code, ra, ru in con.execute(
        "SELECT timestamp, status_code, retry_after_seconds, retry_after_until "
        "FROM platform_request_events WHERE endpoint_class='alpha_list' "
        "ORDER BY timestamp DESC LIMIT 8"
    ):
        print(f"  {ts}  status={code}  retry_after={ra}  until={ru or '-'}")

    if int(status) != 429:
        print(f"\nDECISION: CLEAR_TO_SEND (last alpha_list was HTTP {status}, not 429)")
        return 0

    if until is None:
        print("\nDECISION: CLEAR_TO_SEND (429 recorded no usable retry window)")
        return 0

    remaining = (until - now).total_seconds()
    print(f"\nEFFECTIVE_RETRY_UNTIL = {until.isoformat()}  (from {source})")
    print(f"REMAINING_SECONDS     = {remaining:.1f}")
    if remaining > 0:
        print("\nDECISION: DO_NOT_SEND — still inside the platform retry window")
        return 4
    print(f"\nDECISION: CLEAR_TO_SEND — window expired {-remaining:.0f}s ago")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
