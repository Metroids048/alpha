"""Connectivity readiness result for the deliberately tiny platform probe."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from alpha_mining.auth.session_manager import auth_state_metadata, auth_state_status
from alpha_mining.platform.access import PlatformAccessController, _parse_time


@dataclass(frozen=True)
class PlatformReadiness:
    auth_status: str
    identity_probe: str
    count_probe: str
    list_probe: str
    status_counts: dict[int, int]
    ready_for_ledger_sync: bool

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_readiness(
    *,
    auth_status: str,
    identity_status: str,
    count_status: str,
    list_status: str,
    status_counts: Mapping[int, int],
) -> PlatformReadiness:
    counts = {int(key): int(value) for key, value in status_counts.items()}
    access_error = any(counts.get(code, 0) > 0 for code in (401, 403, 429))
    ready = (
        str(auth_status).upper() == "FRESH"
        and all(str(value).upper() == "PASS" for value in (identity_status, count_status, list_status))
        and not access_error
    )
    return PlatformReadiness(
        str(auth_status).upper(),
        str(identity_status).upper(),
        str(count_status).upper(),
        str(list_status).upper(),
        counts,
        ready,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_connectivity_probe(
    client: Any,
    *,
    database: str | Path = "research_memory.sqlite",
    output_path: str | Path = "platform_readiness.json",
    auth_status_resolver: Callable[[str | Path], str] = auth_state_status,
) -> PlatformReadiness:
    """Run authentication plus exactly three bounded reads and stop on first failure."""
    started = _utc_now()
    controller = PlatformAccessController(database)
    state_before = controller.status()
    until = _parse_time(state_before.retry_after_until)
    recovery_probe = (
        state_before.state == "RATE_LIMITED" and until is not None and started >= until
    )
    identity_status = "SKIPPED"
    count_status = "SKIPPED"
    list_status = "SKIPPED"
    platform_count: int | None = None
    listed_rows: int | None = None
    error_class = ""
    stage = "authentication"
    try:
        client.authenticate()
        stage = "identity"
        client.fetch_identity(recovery_probe=recovery_probe)
        identity_status = "PASS"
        stage = "count"
        probe_filters = {"status": "UNSUBMITTED", "limit": 1, "offset": 0, "order": "-dateCreated"}
        platform_count = int(client.count_alphas(probe_filters))
        count_status = "PASS"
        stage = "list"
        list_payload = client.list_alphas(
            probe_filters
        )
        results = list_payload.get("results")
        if not isinstance(results, list) or len(results) > 1:
            raise ValueError("limit=1 Alpha list returned an invalid schema")
        listed_rows = len(results)
        list_status = "PASS"
    except Exception as exc:
        error_class = type(exc).__name__
        if stage == "identity":
            identity_status = "FAIL"
        elif stage == "count":
            count_status = "FAIL"
        elif stage == "list":
            list_status = "FAIL"

    with sqlite3.connect(database) as con:
        status_counts = {
            int(code): int(count)
            for code, count in con.execute(
                "SELECT status_code,COUNT(*) FROM platform_request_events WHERE timestamp>=? GROUP BY status_code",
                (started.isoformat().replace("+00:00", "Z"),),
            )
        }
    auth_status = str(auth_status_resolver(client.state_path)).upper()
    result = evaluate_readiness(
        auth_status=auth_status,
        identity_status=identity_status,
        count_status=count_status,
        list_status=list_status,
        status_counts=status_counts,
    )
    state_after = controller.status()
    auth_metadata = auth_state_metadata(client.state_path)
    payload = {
        **result.as_dict(),
        "platform_count": platform_count,
        "limit_1_rows": listed_rows,
        "probe_started_at": started.isoformat().replace("+00:00", "Z"),
        "probe_completed_at": _utc_now().isoformat().replace("+00:00", "Z"),
        "error_class": error_class,
        "failed_stage": stage if error_class else "",
        "last_successful_auth": auth_metadata.get("last_successful_auth") or state_after.last_successful_auth,
        "auth_age_seconds": auth_metadata.get("auth_age_seconds"),
        "auth_attempts_today": auth_metadata.get("auth_attempts_today", 0),
        "last_401": state_after.last_401,
        "last_403": state_after.last_403,
        "last_429": state_after.last_429,
        "retry_after_until": state_after.retry_after_until,
        "circuit_state": state_after.state,
        "recovery_attempts": state_after.recovery_attempts,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)
    return result
