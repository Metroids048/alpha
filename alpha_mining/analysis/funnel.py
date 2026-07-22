"""Mutually ordered new-Alpha failure funnel classification."""

from __future__ import annotations

from dataclasses import dataclass


FAILURE_ORDER = (
    "syntax", "operator", "field", "unit", "coverage", "nan", "low_sharpe",
    "low_fitness", "low_turnover", "high_turnover", "sub_universe",
    "concentration", "self_correlation", "description_missing", "platform_error", "unknown",
)

CHECK_CATEGORY = {
    "SYNTAX": "syntax", "INVALID_EXPRESSION": "syntax", "INVALID_OPERATOR": "operator",
    "UNKNOWN_OPERATOR": "operator", "INVALID_FIELD": "field", "UNKNOWN_FIELD": "field",
    "UNIT": "unit", "UNIT_MISMATCH": "unit", "LOW_COVERAGE": "coverage",
    "NAN": "nan", "LOW_SHARPE": "low_sharpe", "LOW_FITNESS": "low_fitness",
    "LOW_TURNOVER": "low_turnover", "HIGH_TURNOVER": "high_turnover",
    "LOW_SUB_UNIVERSE_SHARPE": "sub_universe", "SUB_UNIVERSE": "sub_universe",
    "CONCENTRATED_WEIGHT": "concentration", "CONCENTRATION": "concentration",
    "SELF_CORRELATION": "self_correlation", "DESCRIPTION": "description_missing",
    "DESCRIPTION_MISSING": "description_missing", "PLATFORM_ERROR": "platform_error",
}


@dataclass(frozen=True)
class FailureClassification:
    primary_failure: str
    all_failures: tuple[str, ...]


def classify_failure(checks: list[dict], *, local_failures: list[str] | None = None) -> FailureClassification:
    found = {str(value).lower() for value in (local_failures or []) if str(value).lower() in FAILURE_ORDER}
    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check.get("result") or check.get("status") or "UNKNOWN").upper()
        if status == "PASS":
            continue
        name = str(check.get("name") or check.get("check") or "").upper()
        category = CHECK_CATEGORY.get(name)
        if category is None:
            message = str(check.get("message") or "").upper()
            category = next((value for key, value in CHECK_CATEGORY.items() if key in message), "unknown")
        found.add(category)
    ordered = tuple(item for item in FAILURE_ORDER if item in found)
    return FailureClassification(ordered[0] if ordered else "PASS", ordered)

