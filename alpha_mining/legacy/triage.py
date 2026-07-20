"""Dynamic-gate legacy classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TriageDecision:
    classification: str
    reason: str


def classify_legacy(
    record: Mapping[str, Any],
    *,
    limits: Mapping[str, float],
    near_pass_ratio: float = 0.90,
) -> TriageDecision:
    if not record.get("parse_valid", True) or not record.get("expression", "x"):
        return TriageDecision("ARCHIVE", "invalid_expression")
    if record.get("unit_warnings"):
        return TriageDecision("SEED_ONLY", "unit_warning")
    sharpe, fitness = record.get("sharpe"), record.get("fitness")
    sharpe_limit, fitness_limit = limits.get("LOW_SHARPE"), limits.get("LOW_FITNESS")
    if (
        sharpe is None
        or fitness is None
        or sharpe_limit is None
        or fitness_limit is None
    ):
        return TriageDecision("SEED_ONLY", "missing_metrics_or_gate")
    raw_checks = record.get("checks")
    checks: list[dict[str, Any]] = (
        [check for check in raw_checks if isinstance(check, dict)]
        if isinstance(raw_checks, list)
        else []
    )
    hard = [
        check
        for check in checks
        if str(check.get("result") or "").upper()
        in {"FAIL", "FAILED", "ERROR", "REJECTED"}
    ]
    if float(sharpe) >= sharpe_limit and float(fitness) >= fitness_limit and not hard:
        incomplete = not checks or any(
            str(check.get("result") or "").upper()
            in {"PENDING", "MISSING", "UNKNOWN", ""}
            for check in checks
        )
        return TriageDecision(
            "RECHECK" if incomplete else "SEED_ONLY",
            "checks_incomplete" if incomplete else "historical_pass_seed",
        )
    if (
        float(sharpe) >= sharpe_limit * near_pass_ratio
        and float(fitness) >= fitness_limit * near_pass_ratio
    ):
        return TriageDecision("REPAIR", "near_pass")
    return TriageDecision("SEED_ONLY", "below_near_pass")
