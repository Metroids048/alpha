"""Baseline-first generation and bounded near-pass settings search."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from alpha_mining.simulate.settings_optimizer import SettingTrial, SettingsOptimizer


class BaselineOutcome(str, Enum):
    FAR_FAIL = "FAR_FAIL"
    NEAR_PASS = "NEAR_PASS"
    PASS = "PASS"


def classify_baseline(
    *, sharpe: float, live_threshold: float, near_pass_ratio: float = 0.90
) -> BaselineOutcome:
    threshold = float(live_threshold)
    if float(sharpe) >= threshold:
        return BaselineOutcome.PASS
    if threshold > 0 and float(sharpe) >= threshold * float(near_pass_ratio):
        return BaselineOutcome.NEAR_PASS
    return BaselineOutcome.FAR_FAIL


@dataclass(frozen=True)
class BaselineCandidate:
    candidate_id: str
    hypothesis_id: str
    family: str
    expression: str
    stage: str = "baseline"


class BaselineFirstGenerator:
    def __init__(self, *, near_pass_ratio: float = 0.90) -> None:
        self.near_pass_ratio = float(near_pass_ratio)

    def generate(
        self, *, hypothesis_id: str, family: str, fields: Iterable[str]
    ) -> list[BaselineCandidate]:
        field_list = list(
            dict.fromkeys(str(field).strip() for field in fields if str(field).strip())
        )
        if not field_list:
            return []
        expression = f"group_rank({field_list[0]}/cap,subindustry)-0.5"
        candidate_id = "baseline_" + hashlib.sha256(
            f"{hypothesis_id}\0{expression}".encode("utf-8")
        ).hexdigest()[:20]
        return [BaselineCandidate(candidate_id, hypothesis_id, family, expression)]

    def settings_trials(
        self,
        base: dict[str, Any],
        *,
        outcome: BaselineOutcome,
        candidate_id: str,
    ) -> list[SettingTrial]:
        if outcome is not BaselineOutcome.NEAR_PASS:
            return []
        return SettingsOptimizer(
            max_local_trials=4, total_budget=4, per_candidate_budget=4
        ).local_trials(
            base,
            quality_score=1.0,
            metric_ratio=self.near_pass_ratio,
            delay_allowed=False,
            candidate_id=candidate_id,
            candidate_classification="RECHECK",
        )
