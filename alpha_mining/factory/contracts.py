"""Small immutable contracts shared by the factory and platform gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimulationCheckpoint:
    """External evidence that a simulation was accepted by the platform."""

    progress_location: str = ""
    alpha_id: str = ""


@dataclass(frozen=True)
class ResultValidation:
    valid: bool
    normalized_status: str
    reason: str


def validate_simulation_result(result: Any) -> ResultValidation:
    """Reject empty IDs and non-terminal platform responses in one place."""

    status = str(getattr(result, "status", "") or "").strip().upper()
    alpha_id = str(getattr(result, "alpha_id", "") or "").strip()
    if not alpha_id:
        return ResultValidation(False, status or "UNKNOWN", "alpha_id is empty")
    if status in {"FAILED", "ERROR", "REJECTED"}:
        return ResultValidation(False, status, f"platform status is {status}")
    if status not in {"COMPLETE", "SUCCESS", "SIMULATED"}:
        return ResultValidation(False, status or "UNKNOWN", "status is not a terminal success")
    return ResultValidation(True, status, "valid simulation result")


class SimulationOutcomeUnknown(RuntimeError):
    """The platform may have accepted a POST, but its outcome is unknowable."""


class SimulationAuthenticationPaused(RuntimeError):
    """Authentication expired after a simulation lease was acquired.

    The request remains resumable; callers must not turn this into a terminal
    failure because a persisted progress URL or alpha ID may still be valid.
    """
