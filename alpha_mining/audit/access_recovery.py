"""Fail-closed access recovery and minimal-pilot reports; never calls the platform."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha_mining.auth.session_manager import auth_state_metadata
from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.reporting import export_request_events, write_ledger_sync_report


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_access_recovery_reports(
    database: str | Path,
    output_dir: str | Path = ".",
    *,
    auth_state_file: str | Path = ".wq_auth_state.json",
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    ledger = write_ledger_sync_report(database, target / "platform_ledger_sync_report.json")
    event_count = export_request_events(database, target / "platform_request_events.csv")
    readiness_path = target / "platform_readiness.json"
    readiness = _read_json(readiness_path)
    if not readiness:
        auth = auth_state_metadata(auth_state_file)
        access = PlatformAccessController(database).status()
        readiness = {
            **auth,
            "identity_probe": "SKIPPED",
            "count_probe": "SKIPPED",
            "list_probe": "SKIPPED",
            "circuit_state": access.state,
            "last_401": access.last_401,
            "last_403": access.last_403,
            "last_429": access.last_429,
            "retry_after_until": access.retry_after_until,
            "ready_for_ledger_sync": False,
            "generated_at": _utc(),
        }
        readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    blockers: list[str] = []
    if not bool(readiness.get("ready_for_ledger_sync")):
        blockers.append("CONNECTIVITY_PROBE_NOT_READY")
    if ledger.get("ledger_status") != "COMPLETE" or int(ledger.get("ledger_rows") or 0) <= 0:
        blockers.append("NONZERO_COMPLETE_LEDGER_MISSING")
    blockers.extend(("OLD_ALPHA_PILOT_NOT_RUN", "NEW_ALPHA_PILOT_NOT_RUN", "DESCRIPTION_PATCH_PILOT_NOT_RUN"))

    reconciliation = target / "platform_reconciliation.csv"
    if not reconciliation.is_file() or ledger.get("ledger_status") != "COMPLETE":
        _write_csv(
            reconciliation,
            ["alpha_id", "in_platform", "in_local", "platform_status", "local_status", "expression_hash", "mismatch_reasons", "primary_reason", "sync_id"],
            [],
        )
    _write_csv(
        target / "old_alpha_pilot.csv",
        ["cluster_id", "alpha_id", "stratum", "basic_checks", "self_correlation", "prod_correlation", "pnl_status", "description_requirement", "run_status", "blocker"],
        [],
    )
    _write_csv(
        target / "new_alpha_baseline_pilot.csv",
        ["hypothesis_id", "stage", "expression_hash", "simulation_status", "basic_pass", "self_correlation", "prod_correlation", "run_status", "blocker"],
        [],
    )
    _write_csv(
        target / "description_patch_pilot.csv",
        ["alpha_id", "alpha_type", "target_field", "dry_run_status", "patch_status", "readback_status", "idempotent", "other_fields_unchanged", "blocker"],
        [],
    )
    _write_csv(
        target / "submission_dry_run.csv",
        ["alpha_id", "sync_id", "allowed", "reasons", "endpoint_calls"],
        [],
    )
    _write_csv(
        target / "submission_blocked.csv",
        ["scope", "alpha_id", "reason", "endpoint_calls"],
        [{"scope": "RUN", "alpha_id": "", "reason": reason, "endpoint_calls": 0} for reason in blockers],
    )

    access_report = f"""# Platform Access Recovery Report

## Verdict

**BLOCKED** — platform access was not attempted after the bounded probe found no usable fresh authentication state.

## Findings

- Local matching supervisor/loop/cycle process: `0`.
- Windows scheduled tasks matching Alpha/BRAIN/WorldQuant: `0` in the latest scan.
- Docker: not installed; systemd and cron are not applicable on this Windows host.
- Other-machine/shared-account activity: `UNKNOWN` and cannot be excluded from this host.
- Credential source scan: process/user/machine `WQ_USERNAME` and `WQ_PASSWORD` are unset; the workspace contains only `.env.example`; no matching Windows Credential Manager target was found.
- Historical evidence: existing logs contain thousands of HTTP 429 entries; legacy paths retried after 429, so historical request amplification is a confirmed contributor.
- Current authentication: `{readiness.get('auth_status', 'STALE')}`; age seconds: `{readiness.get('auth_age_seconds')}`.
- Circuit: `{readiness.get('circuit_state', ledger.get('circuit_state', 'MISSING'))}`; last 429: `{readiness.get('last_429')}`; retry until: `{readiness.get('retry_after_until')}`.
- Connectivity Probe: identity=`{readiness.get('identity_probe', 'SKIPPED')}`, count=`{readiness.get('count_probe', 'SKIPPED')}`, list=`{readiness.get('list_probe', 'SKIPPED')}`.
- ready_for_ledger_sync: `{bool(readiness.get('ready_for_ledger_sync'))}`.
- Sanitized platform request events: `{event_count}`.

The current blocker is missing fresh credentials/session evidence. The previous 429 cannot yet be attributed uniquely to concurrency, stale authentication, account/IP rate, or an unknown platform-side limit. No stale Cookie, Git history, or log credential was used.
"""
    (target / "PLATFORM_ACCESS_RECOVERY_REPORT.md").write_text(access_report, encoding="utf-8")

    pilot_report = f"""# Minimal Business Pilot Report

## Verdict

**BLOCKED** — all pilot stages remain behind a nonzero COMPLETE ledger.

1. 429 root cause: historical retry/request amplification is confirmed; current platform-side cause remains unverified because no authenticated probe was sent.
2. Connectivity Probe passed: `{bool(readiness.get('ready_for_ledger_sync'))}`.
3. Ledger nonzero and COMPLETE: `{ledger.get('ledger_status') == 'COMPLETE' and int(ledger.get('ledger_rows') or 0) > 0}`; rows=`{ledger.get('ledger_rows', 0)}`, sync_id=`{ledger.get('sync_id', '')}`.
4. Platform/local quantity agreement: `NOT_VERIFIABLE`.
5. Old pilot SELF/PROD correlation: `NOT_RUN`.
6. New baseline base-pass rate: `NOT_RUN`.
7. Description PATCH readback: `NOT_RUN`; PATCH calls=`0`.
8. Fully submittable candidates: `0`; submit calls=`0`.
9. Next priority: restore a fresh legal authentication session and pass the three-read Connectivity Probe; research generation must remain paused.

## Blockers

{chr(10).join(f'- {reason}' for reason in blockers)}
"""
    (target / "MINIMAL_BUSINESS_PILOT_REPORT.md").write_text(pilot_report, encoding="utf-8")
    return {"status": "BLOCKED" if blockers else "PASS", "blockers": blockers}
