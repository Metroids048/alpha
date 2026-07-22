"""Platform SELF_CORRELATION status and cluster disposition helpers."""

from __future__ import annotations

from enum import Enum


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


def normalize_check_status(value: object) -> CheckStatus:
    text = str(value or "MISSING").upper().strip()
    if text in {"FAILED", "REJECTED"}:
        text = "FAIL"
    try:
        return CheckStatus(text)
    except ValueError:
        return CheckStatus.UNKNOWN


def cluster_disposition(
    representative_statuses: list[str], *, min_explicit_failures: int = 3
) -> str:
    statuses = [normalize_check_status(value) for value in representative_statuses]
    if any(status is CheckStatus.PASS for status in statuses):
        return "SALVAGEABLE"
    explicit_failures = sum(status is CheckStatus.FAIL for status in statuses)
    if explicit_failures >= max(1, int(min_explicit_failures)) and all(
        status is CheckStatus.FAIL for status in statuses
    ):
        return "FROZEN"
    return "OBSERVE_ONLY"
