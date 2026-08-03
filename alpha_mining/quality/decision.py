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
    effective = thresholds or QualityThresholds.with_live_thresholds(live_thresholds)
    reasons: list[str] = []
    if str(status or "").upper() != "COMPLETE":
        return QualityDecision(QualityStatus.FAR_FAIL, ("SIMULATION_NOT_COMPLETE",), effective)
    if not str(alpha_id or "").strip():
        return QualityDecision(QualityStatus.FAR_FAIL, ("ALPHA_ID_MISSING",), effective)

    by_name = {
        str(check.get("name") or "").upper(): str(check.get("result") or check.get("status") or "MISSING").upper()
        for check in checks
        if isinstance(check, Mapping) and str(check.get("name") or "").strip()
    }
    required = {str(name).upper() for name in (mandatory_checks or ())}
    required.update(
        str(check.get("name") or "").upper()
        for check in checks
        if isinstance(check, Mapping) and check.get("mandatory") is True
    )
    required.add("SELF_CORRELATION")
    if prod_correlation_required and not prod_corr_exception_confirmed:
        required.add("PROD_CORRELATION")
    elif prod_corr_exception_confirmed:
        required.discard("PROD_CORRELATION")
    missing = sorted(name for name in required if name not in by_name)
    if missing:
        return QualityDecision(
            QualityStatus.WAITING_CHECKS,
            tuple(f"{name}_MISSING" for name in missing),
            effective,
        )

    hard_fail_names = {
        name
        for name in required
        if by_name.get(name) != "PASS" and name not in {"LOW_SHARPE", "LOW_FITNESS", "HIGH_TURNOVER", "LOW_TURNOVER"}
    }
    for name, value in by_name.items():
        if name in {"CATALOG", "SYNTAX", "DATA_COVERAGE"} and value in {"FAIL", "FAILED", "REJECTED"}:
            hard_fail_names.add(name)
    if hard_fail_names:
        return QualityDecision(
            QualityStatus.FAR_FAIL,
            tuple(f"{name}_{by_name.get(name, 'FAIL')}" for name in sorted(hard_fail_names)),
            effective,
        )

    sharpe = _metric(metrics, "sharpe")
    fitness = _metric(metrics, "fitness")
    turnover = _metric(metrics, "turnover")
    missing_metrics = [name for name, value in (("SHARPE", sharpe), ("FITNESS", fitness), ("TURNOVER", turnover)) if value is None]
    if missing_metrics:
        return QualityDecision(QualityStatus.FAR_FAIL, tuple(f"{name}_MISSING" for name in missing_metrics), effective)

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
