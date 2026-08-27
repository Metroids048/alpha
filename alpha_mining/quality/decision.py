"""Fail-closed quality classification shared by generation and submission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class QualityStatus(str, Enum):
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    WAITING_CHECKS = "WAITING_CHECKS"
    NEAR_PASS = "NEAR_PASS"
    FAR_FAIL = "FAR_FAIL"


@dataclass(frozen=True)
class QualityThresholds:
    sharpe: float = 1.57
    fitness: float = 1.0
    turnover_min: float = 0.01
    turnover_max: float = 0.70

    @classmethod
    def with_live_thresholds(cls, live: Mapping[str, Any] | None = None) -> "QualityThresholds":
        values = dict(live or {})
        return cls(
            sharpe=max(cls.sharpe, _number(values, "sharpe", "low_sharpe", "LOW_SHARPE")),
            fitness=max(cls.fitness, _number(values, "fitness", "low_fitness", "LOW_FITNESS")),
            turnover_min=max(cls.turnover_min, _number(values, "turnover_min", "low_turnover")),
            turnover_max=min(cls.turnover_max, _number(values, "turnover_max", "high_turnover", default=cls.turnover_max)),
        )


@dataclass(frozen=True)
class QualityDecision:
    status: QualityStatus
    reasons: tuple[str, ...]
    thresholds: QualityThresholds
    repairable: bool = False

    @property
    def ready(self) -> bool:
        return self.status is QualityStatus.READY_TO_SUBMIT


_METRIC_CHECKS = frozenset({"LOW_SHARPE", "LOW_FITNESS", "HIGH_TURNOVER", "LOW_TURNOVER"})


def _canonical_check_name(value: Any) -> str:
    name = str(value or "").upper()
    return "PROD_CORRELATION" if name == "PRODUCTION_CORRELATION" else name


def normalize_platform_checks(checks: Any) -> dict[str, str]:
    """Return canonical check names and fail-closed statuses."""

    normalized: dict[str, str] = {}
    if isinstance(checks, Mapping):
        checks = [
            {"name": name, "result": value}
            for name, value in checks.items()
        ]
    if not isinstance(checks, (list, tuple)):
        checks = []
    for check in checks:
        if not isinstance(check, Mapping) or not str(check.get("name") or "").strip():
            continue
        name = _canonical_check_name(check.get("name"))
        status = str(check.get("result") or check.get("status") or "MISSING").upper()
        normalized[name] = _worst_status(normalized.get(name), status)
    return normalized


def _worst_status(current: str | None, candidate: str) -> str:
    """Merge duplicate platform observations without allowing a better result to hide a failure."""

    if current is None:
        return candidate
    rank = {
        "PASS": 0,
        "MISSING": 1,
        "UNKNOWN": 1,
        "PENDING": 1,
        "WAITING": 1,
        "FAIL": 2,
        "FAILED": 2,
        "REJECTED": 2,
        "ERROR": 2,
    }
    return candidate if rank.get(candidate, 2) >= rank.get(current, 2) else current


def blocking_check_reasons(
    checks: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    mandatory_checks: tuple[str, ...] | None = None,
    prod_exception: bool = False,
) -> tuple[QualityStatus | None, tuple[str, ...]]:
    """Classify non-metric platform gates without granting implicit passes."""

    by_name = normalize_platform_checks(checks)
    required = {_canonical_check_name(name) for name in (mandatory_checks or ())}
    required.update(
        _canonical_check_name(check.get("name"))
        for check in checks
        if isinstance(check, Mapping) and check.get("mandatory") is True
    )
    required.add("SELF_CORRELATION")
    if not prod_exception:
        required.add("PROD_CORRELATION")
    ignored = {"PROD_CORRELATION"} if prod_exception else set()
    missing = sorted(name for name in required if name not in by_name and name not in ignored)
    if missing:
        return QualityStatus.WAITING_CHECKS, tuple(f"{name}_MISSING" for name in missing)

    waiting = sorted(
        name for name, value in by_name.items()
        if name not in ignored and value in {"MISSING", "UNKNOWN", "PENDING", "WAITING"}
    )
    if waiting:
        return QualityStatus.WAITING_CHECKS, tuple(f"{name}_{by_name[name]}" for name in waiting)
    near = sorted(
        name for name, value in by_name.items()
        if name not in ignored and value in {"NEAR_PASS", "NEAR"}
    )
    if near:
        return QualityStatus.NEAR_PASS, tuple(f"{name}_{by_name[name]}" for name in near)
    failed = sorted(
        name for name, value in by_name.items()
        if name not in ignored and value != "PASS"
    )
    if failed:
        return QualityStatus.FAR_FAIL, tuple(f"{name}_{by_name[name]}" for name in failed)
    return None, ()


def evaluate_quality(
    *,
    alpha_id: str,
    status: str,
    metrics: Mapping[str, Any],
    checks: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    thresholds: QualityThresholds | None = None,
    live_thresholds: Mapping[str, Any] | None = None,
    mandatory_checks: tuple[str, ...] | None = None,
    prod_correlation_required: bool = True,
    prod_corr_exception_confirmed: bool = False,
) -> QualityDecision:
    """Classify a completed simulation without implicit passes or loose gates."""
    explicit_local_thresholds = thresholds is not None or live_thresholds is not None
    effective = thresholds or QualityThresholds.with_live_thresholds(live_thresholds)
    reasons: list[str] = []
    if str(status or "").upper() != "COMPLETE":
        return QualityDecision(QualityStatus.FAR_FAIL, ("SIMULATION_NOT_COMPLETE",), effective)
    if not str(alpha_id or "").strip():
        return QualityDecision(QualityStatus.FAR_FAIL, ("ALPHA_ID_MISSING",), effective)

    required_for_gate = mandatory_checks
    if not prod_correlation_required:
        required_for_gate = tuple(name for name in (mandatory_checks or ()) if str(name).upper() != "PROD_CORRELATION")
    gate_status, gate_reasons = blocking_check_reasons(
        checks,
        mandatory_checks=required_for_gate,
        prod_exception=prod_corr_exception_confirmed or not prod_correlation_required,
    )
    if gate_status is not None:
        return QualityDecision(gate_status, gate_reasons, effective)

    sharpe = _metric(metrics, "sharpe")
    fitness = _metric(metrics, "fitness")
    turnover = _metric(metrics, "turnover")
    missing_metrics = [name for name, value in (("SHARPE", sharpe), ("FITNESS", fitness), ("TURNOVER", turnover)) if value is None]
    if missing_metrics:
        return QualityDecision(QualityStatus.FAR_FAIL, tuple(f"{name}_MISSING" for name in missing_metrics), effective)

    # The live platform checks are the production source of truth.  Numeric
    # thresholds are retained only for callers that explicitly request the
    # legacy local heuristic (for example a bounded optimizer trial); the
    # default production path never turns a platform PASS into a local FAIL.
    if not explicit_local_thresholds:
        return QualityDecision(QualityStatus.READY_TO_SUBMIT, ("PLATFORM_CHECKS_PASSED",), effective)

    failed_dimensions: list[str] = []
    if sharpe < effective.sharpe:
        failed_dimensions.append("SHARPE_LOW")
    if fitness <= effective.fitness:
        failed_dimensions.append("FITNESS_LOW")
    if turnover < effective.turnover_min:
        failed_dimensions.append("TURNOVER_LOW")
    if turnover > effective.turnover_max:
        failed_dimensions.append("TURNOVER_HIGH")
    if not failed_dimensions:
        return QualityDecision(QualityStatus.READY_TO_SUBMIT, ("ALL_HARD_GATES_PASSED",), effective)

    severe_turnover = turnover > effective.turnover_max and (sharpe < 1.25 or fitness < 0.75)
    near_floor = sharpe >= 1.25 and fitness >= 0.75
    if len(failed_dimensions) == 1 and near_floor and not severe_turnover:
        return QualityDecision(QualityStatus.NEAR_PASS, tuple(failed_dimensions), effective, repairable=True)
    return QualityDecision(QualityStatus.FAR_FAIL, tuple(failed_dimensions), effective)


def _number(values: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in values and values[name] is not None:
            try:
                return float(values[name])
            except (TypeError, ValueError):
                continue
    return float(default)


def _metric(metrics: Mapping[str, Any], name: str) -> float | None:
    try:
        value = metrics.get(name)
        return None if value is None else float(value)
    except (AttributeError, TypeError, ValueError):
        return None
