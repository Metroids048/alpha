"""Fail-closed submission guard for consultant candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
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
        for name in context.mandatory_checks:
            status = by_name.get(name.upper(), "MISSING")
            if status != PASS:
                reasons.append(f"MANDATORY_{name.upper()}_{status}")
        self_status = by_name.get("SELF_CORRELATION", "MISSING")
        if self_status != PASS:
            reasons.append(f"SELF_CORRELATION_{self_status}")
        for correlation_name in ("PROD_CORRELATION", "PRODUCTION_CORRELATION"):
            if correlation_name in by_name and by_name[correlation_name] != PASS:
                reasons.append(f"{correlation_name}_{by_name[correlation_name]}")
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
        return GuardDecision(not reasons, tuple(dict.fromkeys(reasons)))
