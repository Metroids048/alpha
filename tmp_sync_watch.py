"""Sync progress watcher — TEMPORARY_VALIDATION_HARNESS / NOT_FOR_COMMIT.

Emits one stdout line per event so a Monitor can surface it. Watches the shared
PLATFORM_ACCESS_DB (not VAL_ROOT) because that is where platform_request_events
lives. Read-only; never writes.

Coverage: emits on every terminal outcome, not just success — non-200 statuses,
error_class values (ProxyError etc.), process death, and cache landing. Silence
means "still running normally", and that is only trustworthy because process
death is itself an emitted event.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCESS_DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
VAL_ROOT = ROOT / ".validation_workspace"
CACHE_NAMES = (
    ".alpha_datasets_cache.json",
    ".alpha_datafields_cache.json",
    ".alpha_operators_cache.json",
)
POLL_S = 30.0
PROGRESS_EVERY_S = 300.0

# Only count events from this watcher's own start. A hardcoded window pulled in
# pre-existing 401s from an earlier session and reported them as fresh failures.
SINCE = (
    datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
)


def driver_alive() -> bool:
    """True while a tmp_validation_driver process is still running."""
    import subprocess

    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" "
                "| Where-Object { $_.CommandLine -like '*tmp_validation_driver*' } "
                "| Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return True  # never declare death on a probe failure


def snapshot() -> tuple[int, list[tuple], str]:
    uri = f"file:{ACCESS_DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        ok = con.execute(
            "SELECT COUNT(*) FROM platform_request_events "
            "WHERE timestamp >= ? AND status_code = 200",
            (SINCE,),
        ).fetchone()[0]
        bad = con.execute(
            "SELECT status_code, error_class, COUNT(*), MAX(timestamp) "
            "FROM platform_request_events WHERE timestamp >= ? AND status_code != 200 "
            "GROUP BY status_code, error_class",
            (SINCE,),
        ).fetchall()
        state = con.execute(
            "SELECT state FROM platform_access_state WHERE singleton=1"
        ).fetchone()[0]
    return int(ok), list(bad), str(state)


def caches_present() -> list[str]:
    return [name for name in CACHE_NAMES if (VAL_ROOT / name).is_file()]


def checkpoint_progress() -> str:
    """Completed datasets = legal envelopes on disk (INFRA-REL-001 authority)."""
    fields = VAL_ROOT / ".alpha_catalog_sync_checkpoint" / "fields"
    manifest = VAL_ROOT / ".alpha_catalog_sync_checkpoint" / "manifest.json"
    if not manifest.is_file():
        return "checkpoint=absent"
    total = "?"
    try:
        import json

        total = str(len(json.loads(manifest.read_text(encoding="utf-8"))["dataset_ids"]))
    except Exception:  # noqa: BLE001 - progress display must never crash the watch
        pass
    done = len(list(fields.glob("*.json"))) if fields.is_dir() else 0
    return f"datasets_done={done}/{total}"


def main() -> int:
    print(f"watch start: access_db={ACCESS_DB}", flush=True)
    seen_bad: set[tuple] = set()
    last_progress = 0.0
    last_ok = -1

    while True:
        try:
            ok, bad, state = snapshot()
        except Exception as exc:  # noqa: BLE001
            print(f"WATCH_ERROR {type(exc).__name__}: {exc}", flush=True)
            time.sleep(POLL_S)
            continue

        for row in bad:
            key = (row[0], row[1])
            if key not in seen_bad:
                seen_bad.add(key)
                print(
                    f"FAILURE status={row[0]} err={row[1] or '-'} count={row[2]} latest={row[3]}",
                    flush=True,
                )

        if state != "CLOSED":
            print(f"CIRCUIT state={state}", flush=True)

        present = caches_present()
        if len(present) == len(CACHE_NAMES):
            print(f"CACHE_LANDED all 3 files in {VAL_ROOT}  requests_200={ok}  {checkpoint_progress()}", flush=True)
            return 0

        if not driver_alive():
            print(
                f"DRIVER_EXITED requests_200={ok} caches_present={len(present)}/3 state={state} {checkpoint_progress()}",
                flush=True,
            )
            return 1

        now = time.monotonic()
        if now - last_progress >= PROGRESS_EVERY_S and ok != last_ok:
            print(f"progress requests_200={ok} state={state} {checkpoint_progress()}", flush=True)
            last_progress = now
            last_ok = ok

        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
