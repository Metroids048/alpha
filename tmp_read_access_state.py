"""Read-only platform access state dump — TEMPORARY_VALIDATION_HARNESS / NOT_FOR_COMMIT.

Opens research_memory.sqlite with mode=ro so nothing can be written. Deliberately
does NOT instantiate PlatformAccessController, because its __init__ runs migrate()
plus an UPDATE of max_auto_recoveries — both writes.

Session TTL is derived from .wq_auth_state.json metadata only. Cookie values are
never read into memory or printed; only expiry timestamps and name presence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# The live account-level circuit lives in the nested operational database that
# tmp_validation_driver.py:32 actually wires into ReadOnlyPlatformClient.  The
# repo-root research_memory.sqlite is a stale 2026-08-06 leftover; reading it
# reports state=CLOSED and hides tonight's real 429 history.
_ROOT = Path(__file__).resolve().parent
DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
AUTH_STATE = _ROOT / ".wq_auth_state.json"

COLUMNS = (
    "state",
    "opened_at",
    "retry_after_until",
    "recovery_attempts",
    "max_auto_recoveries",
    "last_successful_auth",
    "last_401",
    "last_403",
    "last_429",
    "last_request_id",
    "last_session_id",
    "reason",
    "updated_at",
)


def parse_iso(text: object) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dump_access_state(now: datetime) -> None:
    # Provenance rule: never report a circuit state without its source path.
    print("=== provenance ===")
    print(f"  EFFECTIVE_ROOT         = {_ROOT}")
    print(f"  EFFECTIVE_DATABASE     = {DB}")
    print(f"  DATABASE_EXISTS        = {DB.exists()}")
    if DB.exists():
        stat = DB.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        print(f"  DATABASE_BYTES         = {stat.st_size}")
        print(f"  DATABASE_MTIME_UTC     = {mtime}")
    print()
    if not DB.exists():
        print(f"DB MISSING: {DB}")
        return
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        row = con.execute(
            f"SELECT {','.join(COLUMNS)} FROM platform_access_state WHERE singleton=1"
        ).fetchone()
        recent = con.execute(
            "SELECT timestamp,endpoint_class,method,status_code,retry_after_until,error_class "
            "FROM platform_request_events ORDER BY timestamp DESC LIMIT 8"
        ).fetchall()

    if row is None:
        print("platform_access_state: NO ROW")
        return

    print("=== platform_access_state (read-only) ===")
    state_map = dict(zip(COLUMNS, row))
    for key in COLUMNS:
        print(f"  {key:22s} = {state_map[key]!r}")

    state = str(state_map["state"])
    until = parse_iso(state_map["retry_after_until"])
    print()
    print("=== cooldown evaluation ===")
    print(f"  now(utc)               = {now.isoformat()}")
    print(f"  state                  = {state}")
    print(f"  retry_after_until      = {until.isoformat() if until else None}")
    if state == "RATE_LIMITED":
        if until is None:
            print("  VERDICT: RATE_LIMITED with no retry_after_until -> manual review required")
        elif now < until:
            remaining = (until - now).total_seconds()
            print(f"  remaining_seconds      = {remaining:.1f}")
            print("  VERDICT: RATE_LIMIT_COOLDOWN_ACTIVE")
        else:
            print(f"  expired_by_seconds     = {(now - until).total_seconds():.1f}")
            print("  VERDICT: cooldown expired -> ONE explicit GET recovery probe allowed")
    elif state == "CLOSED":
        print("  VERDICT: CLOSED -> ONE identity GET sanity probe allowed")
    elif state == "HALF_OPEN":
        print("  VERDICT: HALF_OPEN -> a recovery probe is already in flight; do not send")
    else:
        print(f"  VERDICT: unexpected state={state}")

    print()
    print("=== last 8 platform_request_events ===")
    if not recent:
        print("  (none)")
    for ts, cls, method, code, until_text, err in recent:
        print(f"  {ts}  {method:5s} {cls:24s} status={code:<5} retry_until={until_text or '-'} err={err or '-'}")


def dump_session_ttl(now: datetime) -> None:
    print()
    print("=== auth session TTL (metadata only, no cookie values) ===")
    if not AUTH_STATE.exists():
        print(f"  {AUTH_STATE} MISSING")
        return
    try:
        payload = json.loads(AUTH_STATE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  unreadable: {type(exc).__name__}: {exc}")
        return

    print(f"  top-level keys        = {sorted(payload.keys())}")
    # generation / last_auth_utc are the two fields that prove a headed scan
    # actually landed a new session.  account_fingerprint and
    # cookie_blob_dpapi_b64 are never printed, only their presence.
    for key in (
        "version",
        "generation",
        "last_auth_utc",
        "utc_date",
        "auth_attempts",
        "saved_at",
        "created_at",
        "updated_at",
        "expires_at",
        "jwt_expires_at",
    ):
        if key in payload:
            print(f"  {key:21s} = {payload[key]!r}")
    for key in ("account_fingerprint", "cookie_blob_dpapi_b64"):
        value = payload.get(key)
        present = isinstance(value, str) and bool(value.strip())
        print(f"  {key:21s} = <redacted, present={present}>")

    stamp = parse_iso(payload.get("last_auth_utc"))
    if stamp is not None:
        print(f"  session_age_seconds   = {(now - stamp).total_seconds():.1f}")

    cookies = payload.get("cookies")
    if isinstance(cookies, list):
        print(f"  cookie_count          = {len(cookies)}")
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "?")
            expires = item.get("expires") or item.get("expiry") or item.get("expirationDate")
            ttl = ""
            if isinstance(expires, (int, float)):
                exp_dt = datetime.fromtimestamp(float(expires), tz=timezone.utc)
                ttl = f" ttl={(exp_dt - now).total_seconds():.0f}s expires={exp_dt.isoformat()}"
            elif expires:
                exp_dt = parse_iso(expires)
                if exp_dt:
                    ttl = f" ttl={(exp_dt - now).total_seconds():.0f}s expires={exp_dt.isoformat()}"
            print(f"    - {name}{ttl}")


def dump_run_volume() -> None:
    """Aggregate catalog request volume per sync attempt (read-only).

    Sizes the checkpoint design: how many requests each aborted run burned and
    where it died. Groups events into runs by any gap larger than 10 minutes.
    """
    if not DB.exists():
        return
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            "SELECT timestamp,status_code FROM platform_request_events "
            "WHERE endpoint_class='catalog' ORDER BY timestamp ASC"
        ).fetchall()
    print("=== catalog request volume per run (gap > 10min starts a new run) ===")
    print(f"  EFFECTIVE_DATABASE     = {DB}")
    if not rows:
        print("  (no catalog events)")
        print()
        return
    runs: list[dict[str, object]] = []
    previous: datetime | None = None
    for stamp, status in rows:
        moment = parse_iso(stamp)
        if moment is None:
            continue
        if previous is None or (moment - previous).total_seconds() > 600:
            runs.append({"start": moment, "end": moment, "total": 0, "ok": 0, "codes": {}})
        run = runs[-1]
        run["end"] = moment
        run["total"] = int(run["total"]) + 1
        if status is not None and 200 <= int(status) < 300:
            run["ok"] = int(run["ok"]) + 1
        codes = run["codes"]
        assert isinstance(codes, dict)
        key = str(status)
        codes[key] = codes.get(key, 0) + 1
        previous = moment
    for index, run in enumerate(runs[-6:], start=max(1, len(runs) - 5)):
        start = run["start"]
        end = run["end"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        minutes = (end - start).total_seconds() / 60.0
        codes = run["codes"]
        assert isinstance(codes, dict)
        breakdown = " ".join(f"{code}x{count}" for code, count in sorted(codes.items()))
        print(
            f"  run#{index}: {start.isoformat()} -> {end.isoformat()}  "
            f"{minutes:7.1f}min  requests={run['total']:5}  2xx={run['ok']:5}  [{breakdown}]"
        )
    print(f"  total runs observed   = {len(runs)}")
    print()


def main() -> int:
    now = datetime.now(timezone.utc)
    dump_access_state(now)
    dump_run_volume()
    dump_session_ttl(now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
