"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

STEP 10: real platform simulation of queued validation candidates.

Calls PlatformGateway.simulate() only.  It never calls submit_alpha, never
touches factory_control, never writes platform_access_state, and never mocks a
response.  execute_submit is asserted 0 before the first request.

Business state (queue) is read from VAL_ROOT.  The platform access circuit and
auth state stay on the account-level production paths, because a fresh
VAL_ROOT circuit would be seeded CLOSED and discard real 429 history.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.gateway import PlatformGateway

VAL_ROOT = _ROOT / ".validation_workspace"
QUEUE = VAL_ROOT / "待提交Alpha列表.csv"
SETTINGS_SCHEMA = VAL_ROOT / ".alpha_simulation_settings_cache.json"
DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
STATE = _ROOT / ".wq_auth_state.json"
REPORT = _ROOT / "tmp_simulate_report.json"

_SETTING_KEYS = (
    "region", "universe", "delay", "decay", "neutralization",
    "truncation", "language", "pasteurization", "unitHandling", "visualization",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_line(tag: str, controller: PlatformAccessController) -> None:
    s = controller.status()
    print(
        f"  [{tag}] circuit={s.state} retry_after={s.retry_after_until or 'none'} "
        f"attempts={s.recovery_attempts}/{s.max_auto_recoveries}"
    )


def _rows() -> list[dict[str, str]]:
    with QUEUE.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _settings(row: dict[str, str], contract) -> dict[str, object]:
    """Queue-row settings, completed with the keys the endpoint requires.

    The queue CSV has no column for instrumentType / pasteurization /
    unitHandling / nanHandling / visualization, but the endpoint refuses a
    payload without them ("This field is required.").  The queue's own decisions
    (region, universe, delay, decay, neutralization, truncation, language) are
    taken as-is and never overridden; the rest come from the synced schema
    defaults, except instrumentType which the schema does not enumerate at all
    and which production hardcodes as EQUITY in SettingsOptimizer.stage1_default.
    """

    out: dict[str, object] = {}
    for key in _SETTING_KEYS:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        if key in {"delay", "decay"}:
            out[key] = int(raw)
        elif key == "truncation":
            out[key] = float(raw)
        elif key == "visualization":
            out[key] = str(raw).strip().lower() in {"1", "true", "yes"}
        else:
            out[key] = raw
    for key in ("pasteurization", "unitHandling", "nanHandling", "visualization"):
        if key not in out and key in contract.defaults:
            out[key] = contract.defaults[key]
    out.setdefault("instrumentType", "EQUITY")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--min-interval", type=float, default=3.0)
    # The pre-fix candidates sit at the front of the queue.  Selecting by
    # candidate_id prefix keeps this round on post-fix rows without mutating,
    # reordering or re-statusing the queue.
    parser.add_argument("--candidate", action="append", default=[])
    args = parser.parse_args()

    if not os.environ.get("WQ_USERNAME") or not os.environ.get("WQ_PASSWORD"):
        print("BLOCKED: WQ_USERNAME / WQ_PASSWORD absent from project-root .env")
        return 2

    con = sqlite3.connect(str(DB))
    hard_stop, execute_submit = con.execute(
        "SELECT hard_stop, execute_submit FROM factory_control WHERE singleton=1"
    ).fetchone()
    con.close()
    print(f"=== submit guard ===\n  hard_stop={hard_stop} execute_submit={execute_submit}")
    if execute_submit:
        print("BLOCKED: execute_submit is not 0; refusing to run")
        return 2
    print("  submit path: NOT CALLED by this harness (simulate only)")

    controller = PlatformAccessController(str(DB), str(LOCK))
    _state_line("before", controller)
    if controller.status().state == "OPEN":
        print("BLOCKED: access circuit OPEN")
        return 3

    rows = _rows()
    pending = [r for r in rows if str(r.get("queue_status") or "") == "PENDING_SIMULATION"]
    if args.candidate:
        wanted = tuple(args.candidate)
        pending = [
            r for r in pending if str(r.get("candidate_id") or "").startswith(wanted)
        ]
        print(f"  candidate filter: {', '.join(wanted)}")
    print(f"\n=== queue ===\n  rows={len(rows)} pending={len(pending)} taking={min(args.limit, len(pending))}")
    if not pending:
        print("BLOCKED: no PENDING_SIMULATION rows in the validation queue")
        return 4

    gateway = PlatformGateway(
        state_path=str(STATE),
        database=str(DB),
        lock_path=str(LOCK),
        min_interval=args.min_interval,
        timeout=60.0,
        settings_schema_path=str(SETTINGS_SCHEMA),
    )

    results: list[dict[str, object]] = []
    for index, row in enumerate(pending[: args.limit], 1):
        expression = str(row.get("expression") or "").strip()
        settings = _settings(row, gateway.simulation_settings_contract)
        candidate = str(row.get("candidate_id") or "")[:16]
        print(f"\n=== simulate {index}/{min(args.limit, len(pending))} ===")
        print(f"  candidate_id  {candidate}")
        print(f"  expression    {expression}")
        print(f"  settings      {json.dumps(settings, sort_keys=True)}")
        started = time.monotonic()
        record: dict[str, object] = {
            "candidate_id": candidate,
            "expression": expression,
            "settings": settings,
            "started_at": _now(),
        }
        try:
            outcome = gateway.simulate(
                expression=expression,
                settings=settings,
                alpha_type=str(row.get("alpha_type") or "REGULAR"),
            )
        except Exception as exc:  # noqa: BLE001 - classification is the point
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED  {record['error']}")
        else:
            metrics = dict(getattr(outcome, "metrics", {}) or {})
            checks = list(getattr(outcome, "checks", []) or [])
            raw = getattr(outcome, "raw", {}) or {}
            record.update(
                alpha_id=getattr(outcome, "alpha_id", ""),
                status=getattr(outcome, "status", ""),
                metrics=metrics,
                checks=checks,
                # The platform's own reason for a non-COMPLETE status.  Bounded
                # and field-selected: no headers, no cookies, no full body.
                platform_message=str(raw.get("message") or "")[:400],
                platform_status_fields={
                    key: raw.get(key)
                    for key in ("status", "state", "type", "id", "parent", "location")
                    if key in raw
                },
            )
            if record["platform_message"]:
                print(f"  message       {record['platform_message']}")
            if record["platform_status_fields"]:
                print(f"  raw fields    {json.dumps(record['platform_status_fields'], ensure_ascii=False)}")
            print(f"  alpha_id      {record['alpha_id']}")
            print(f"  status        {record['status']}")
            for key in ("sharpe", "fitness", "turnover", "returns", "drawdown", "margin", "longCount", "shortCount"):
                if key in metrics:
                    print(f"  {key:<13} {metrics[key]}")
            for check in checks:
                if isinstance(check, dict):
                    print(f"  check         {check.get('name')}={check.get('result')} "
                          f"value={check.get('value')} limit={check.get('limit')}")
            if record["alpha_id"]:
                print(f"  result url    https://platform.worldquantbrain.com/alpha/{record['alpha_id']}")
        record["elapsed_seconds"] = round(time.monotonic() - started, 1)
        results.append(record)
        _state_line(f"after#{index}", controller)

    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {REPORT}")
    print("submit_alpha calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
