"""Fail-closed submission guard for consultant candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

PASS = "PASS"


@dataclass(frozen=True)
class CandidateContext:
    alpha_id: str
    expression_id: str
    checks: list[dict[str, Any]]
    gate_snapshots_fresh: bool
    quality_buffer_pass: bool
    local_correlation_status: str
    unit_warnings: tuple[str, ...] = ()
    concentration_failure: bool = False
    duplicate: bool = False
    submitted_cluster: bool = False
    mandatory_checks: tuple[str, ...] = ("LOW_SHARPE",)
    competition_required: bool = False
    theme_required: bool = False
    pyramid_required: bool = False
    metrics: dict[str, float] = field(default_factory=dict)
    ledger_status: str = ""
    ledger_synced_at: str = ""
    ledger_sync_id: str = ""
    candidate_sync_id: str = ""
    ledger_freshness_hours: float = 24.0
    description_valid: bool = True
    platform_status: str = "UNSUBMITTED"
    description_status: str = ""
    prod_correlation_required: bool = False
    prod_corr_exception_confirmed: bool = False
    write_intent_statuses: tuple[str, ...] = ()
    execute_submit_enabled: bool | None = None


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reasons: tuple[str, ...]


class SubmissionGuard:
    def evaluate(self, context: CandidateContext) -> GuardDecision:
        reasons: list[str] = []
        by_name = {
            str(check.get("name") or "").upper(): str(
                check.get("result") or check.get("status") or "UNKNOWN"
            ).upper()
            for check in context.checks
            if isinstance(check, dict)
        }
        if not by_name:
            reasons.append("CHECKS_MISSING")
        mandatory = {name.upper() for name in context.mandatory_checks}
        mandatory.update(
            str(check.get("name") or "").upper()
            for check in context.checks
            if isinstance(check, dict) and check.get("mandatory") is True
        )
        for name in sorted(mandatory):
            status = by_name.get(name.upper(), "MISSING")
            if status != PASS:
                reasons.append(f"MANDATORY_{name.upper()}_{status}")
        self_status = by_name.get("SELF_CORRELATION", "MISSING")
        if self_status != PASS:
            reasons.append(f"SELF_CORRELATION_{self_status}")
        if context.prod_correlation_required:
            prod_status = by_name.get(
                "PROD_CORRELATION", by_name.get("PRODUCTION_CORRELATION", "MISSING")
            )
            if prod_status != PASS and not context.prod_corr_exception_confirmed:
                reasons.append(f"PROD_CORRELATION_{prod_status}")
        for required, name in (
            (context.competition_required, "MATCHES_COMPETITION"),
            (context.theme_required, "MATCHES_THEME"),
            (context.pyramid_required, "MATCHES_PYRAMID"),
        ):
            if required and by_name.get(name, "MISSING") != PASS:
                reasons.append(f"{name}_{by_name.get(name, 'MISSING')}")
        for name, status in by_name.items():
            # Unknown future checks are fail-closed too: only an explicit PASS is complete.
            if status != PASS and not any(
                reason.startswith(name) or name in reason for reason in reasons
            ):
                reasons.append(f"CHECK_{name or 'UNKNOWN'}_{status}")
        if not context.gate_snapshots_fresh:
            reasons.append("GATE_SNAPSHOT_STALE_OR_MISSING")
        if not context.quality_buffer_pass:
            reasons.append("QUALITY_BUFFER_FAILED")
        local = context.local_correlation_status.upper()
        if local == "INSUFFICIENT_HISTORY":
            reasons.append("LOCAL_CORRELATION_INSUFFICIENT_HISTORY")
        elif local != "PASS":
            reasons.append(f"LOCAL_CORRELATION_{local or 'MISSING'}")
        if context.unit_warnings:
            reasons.append("UNIT_WARNING")
        if context.concentration_failure:
            reasons.append("CONCENTRATION_FAILURE")
        if context.duplicate:
            reasons.append("DUPLICATE")
        if context.submitted_cluster:
            reasons.append("SUBMITTED_CLUSTER")
        if context.description_status and context.description_status.upper() not in {
            "VERIFIED",
            "NOT_REQUIRED",
        }:
            reasons.append("DESCRIPTION_NOT_VERIFIED")
        elif not context.description_valid:
            reasons.append("DESCRIPTION_INVALID_OR_MISSING")
        if str(context.platform_status or "UNKNOWN").upper() != "UNSUBMITTED":
            reasons.append(f"PLATFORM_STATUS_{str(context.platform_status or 'UNKNOWN').upper()}")
        for write_status in context.write_intent_statuses:
            normalized = str(write_status or "UNKNOWN").upper()
            if normalized in {"PENDING", "PROCESSING", "UNCERTAIN", "UNKNOWN"}:
                reasons.append(f"WRITE_INTENT_{normalized}")
        if context.execute_submit_enabled is False:
            reasons.append("EXECUTE_SUBMIT_DISABLED")
        ledger_fresh = context.ledger_status.upper() == "COMPLETE"
        try:
            synced = datetime.fromisoformat(context.ledger_synced_at.replace("Z", "+00:00"))
            if synced.tzinfo is None:
                synced = synced.replace(tzinfo=timezone.utc)
            ledger_fresh = ledger_fresh and synced >= datetime.now(timezone.utc) - timedelta(hours=context.ledger_freshness_hours)
        except (AttributeError, TypeError, ValueError):
            ledger_fresh = False
        if not ledger_fresh:
            reasons.append("PLATFORM_LEDGER_STALE_OR_MISSING")
        if not context.ledger_sync_id or not context.candidate_sync_id:
            reasons.append("PLATFORM_LEDGER_SYNC_MISSING")
        elif context.ledger_sync_id != context.candidate_sync_id:
            reasons.append("PLATFORM_LEDGER_SYNC_MISMATCH")
        return GuardDecision(not reasons, tuple(dict.fromkeys(reasons)))
