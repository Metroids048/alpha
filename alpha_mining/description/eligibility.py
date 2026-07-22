"""Mutually exclusive historical and new-Alpha eligibility classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class EligibilityStatus(str, Enum):
    SUBMIT_READY = "SUBMIT_READY"
    SUBMIT_READY_EXCEPT_DESCRIPTION = "SUBMIT_READY_EXCEPT_DESCRIPTION"
    STALE_CHECKS = "STALE_CHECKS"
    BASE_GATE_FAILED = "BASE_GATE_FAILED"
    SELF_CORR_FAILED = "SELF_CORR_FAILED"
    PROD_CORR_FAILED = "PROD_CORR_FAILED"
    DESCRIPTION_SCHEMA_UNKNOWN = "DESCRIPTION_SCHEMA_UNKNOWN"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    UNKNOWN_BLOCKED = "UNKNOWN_BLOCKED"


@dataclass(frozen=True)
class EligibilityDecision:
    status: EligibilityStatus
    reasons: tuple[str, ...] = ()


def _checks(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(check.get("name") or check.get("check") or "").upper(): str(
            check.get("result") or check.get("status") or "UNKNOWN"
        ).upper()
        for check in row.get("checks", [])
        if isinstance(check, dict) and str(check.get("name") or check.get("check") or "").strip()
    }


def classify_alpha(row: Mapping[str, Any]) -> EligibilityDecision:
    status = str(row.get("platform_status") or "UNKNOWN").upper()
    if status not in {"UNSUBMITTED", "UNKNOWN", ""}:
        return EligibilityDecision(EligibilityStatus.ALREADY_SUBMITTED, (f"PLATFORM_{status}",))
    if bool(row.get("submission_pending")) or bool(row.get("uncertain_write")):
        return EligibilityDecision(EligibilityStatus.SUBMISSION_PENDING, ("SUBMISSION_PENDING",))
    if not bool(row.get("checks_fresh")):
        return EligibilityDecision(EligibilityStatus.STALE_CHECKS, ("CHECKS_STALE_OR_MISSING",))

    checks = _checks(row)
    base_failures = sorted(
        name
        for name, result in checks.items()
        if name not in {"SELF_CORRELATION", "PROD_CORRELATION", "PRODUCTION_CORRELATION", "DESCRIPTION"}
        and result in {"FAIL", "FAILED", "REJECTED"}
    )
    if base_failures:
        return EligibilityDecision(EligibilityStatus.BASE_GATE_FAILED, tuple(base_failures))
    if checks.get("SELF_CORRELATION") in {"FAIL", "FAILED", "REJECTED"}:
        return EligibilityDecision(EligibilityStatus.SELF_CORR_FAILED, ("SELF_CORRELATION",))
    prod = checks.get("PROD_CORRELATION", checks.get("PRODUCTION_CORRELATION", "MISSING"))
    if prod in {"FAIL", "FAILED", "REJECTED"} and not bool(row.get("prod_corr_exception_confirmed")):
        return EligibilityDecision(EligibilityStatus.PROD_CORR_FAILED, ("PROD_CORRELATION",))

    non_pass = sorted(
        name
        for name, result in checks.items()
        if result != "PASS"
        and not (name == "DESCRIPTION" and bool(row.get("description_required")))
    )
    if non_pass and not (
        set(non_pass) <= {"PROD_CORRELATION", "PRODUCTION_CORRELATION"}
        and bool(row.get("prod_corr_exception_confirmed"))
    ):
        return EligibilityDecision(EligibilityStatus.UNKNOWN_BLOCKED, tuple(non_pass))
    if not checks:
        return EligibilityDecision(EligibilityStatus.UNKNOWN_BLOCKED, ("CHECKS_MISSING",))
    if bool(row.get("description_required")) and not bool(row.get("schema_known")):
        return EligibilityDecision(
            EligibilityStatus.DESCRIPTION_SCHEMA_UNKNOWN, ("DESCRIPTION_SCHEMA_UNKNOWN",)
        )
    if bool(row.get("description_required")) and not bool(row.get("description_valid")):
        return EligibilityDecision(
            EligibilityStatus.SUBMIT_READY_EXCEPT_DESCRIPTION,
            ("DESCRIPTION_REQUIRED",),
        )
    return EligibilityDecision(EligibilityStatus.SUBMIT_READY)
