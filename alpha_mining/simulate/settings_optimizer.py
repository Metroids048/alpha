"""Three-stage settings selection without Cartesian-product search."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SettingTrial:
    profile: str
    settings: dict[str, Any]
    parameter_delta: dict[str, Any]
    purpose: str = "ROBUSTNESS_ONLY"


class SettingsOptimizer:
    def __init__(
        self,
        *,
        max_local_trials: int = 6,
        total_budget: int = 64,
        per_candidate_budget: int | None = None,
    ) -> None:
        self.max_local_trials = max(0, int(max_local_trials))
        self.total_budget = max(0, int(total_budget))
        self.per_candidate_budget = max(
            0,
            int(
                self.max_local_trials
                if per_candidate_budget is None
                else per_candidate_budget
            ),
        )
        self.consumed = 0
        self._candidate_reserved: dict[str, int] = {}

    def stage1_default(
        self, family: str, priors: list[dict] | None = None
    ) -> dict[str, Any]:
        defaults = {
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "neutralization": "SUBINDUSTRY",
            "decay": 0,
            "truncation": 0.08,
            "nanHandling": "ON",
            "pasteurization": "ON",
        }
        viable = sorted(
            priors or [],
            key=lambda item: (
                float(item.get("smoothed_success", 0)),
                -float(item.get("simulation_cost", 0)),
            ),
            reverse=True,
        )
        return {**defaults, **(viable[0].get("settings", {}) if viable else {})}

    def local_trials(
        self,
        base: dict[str, Any],
        *,
        quality_score: float,
        metric_ratio: float,
        delay_allowed: bool = False,
        candidate_id: str = "__single_candidate__",
        candidate_classification: str = "RECHECK",
    ) -> list[SettingTrial]:
        if str(candidate_classification).upper() == "ARCHIVE":
            return []
        # Only near-pass candidates or candidates with an explicit high-potential
        # score receive local search budget.
        if quality_score < 0.7 and metric_ratio < 0.90:
            return []
        candidates = [
            (
                "neutralization",
                "INDUSTRY"
                if base.get("neutralization") != "INDUSTRY"
                else "SUBINDUSTRY",
            ),
            ("decay", 2 if int(base.get("decay", 0)) != 2 else 4),
            (
                "truncation",
                0.06 if float(base.get("truncation", 0.08)) != 0.06 else 0.10,
            ),
            ("nanHandling", "OFF" if base.get("nanHandling") == "ON" else "ON"),
            (
                "pasteurization",
                "OFF" if base.get("pasteurization", "ON") == "ON" else "ON",
            ),
        ]
        if delay_allowed:
            candidates.append(("delay", 0 if int(base.get("delay", 1)) == 1 else 1))
        out = []
        available = max(0, self.total_budget - self.consumed)
        candidate_key = str(candidate_id or "__missing_candidate__")
        candidate_available = max(
            0,
            self.per_candidate_budget - self._candidate_reserved.get(candidate_key, 0),
        )
        limit = min(self.max_local_trials, available, candidate_available)
        for key, value in candidates[:limit]:
            settings = dict(base)
            settings[key] = value
            purpose = (
                "STABILITY_TURNOVER_ONLY"
                if key in {"decay", "truncation"}
                else "ROBUSTNESS_ONLY"
            )
            out.append(SettingTrial(f"ofat_{key}", settings, {key: value}, purpose))
        reserved = len(out)
        self._candidate_reserved[candidate_key] = (
            self._candidate_reserved.get(candidate_key, 0) + reserved
        )
        self.consumed = min(self.total_budget, self.consumed + reserved)
        return out

    def consume(self, count: int = 1) -> None:
        self.consumed = min(self.total_budget, self.consumed + max(0, int(count)))

    def budget_status(self) -> str:
        return "BUDGET_EXHAUSTED" if self.consumed >= self.total_budget else "AVAILABLE"

    def candidate_budget_status(self, candidate_id: str) -> str:
        used = self._candidate_reserved.get(str(candidate_id), 0)
        if used == 0:
            return "UNUSED"
        return "BUDGET_EXHAUSTED" if used >= self.per_candidate_budget else "AVAILABLE"

    def persist_result(
        self,
        database: str | Path,
        *,
        expression_id: str,
        trial: SettingTrial,
        metrics: dict,
        checks: list[dict],
        quality_score: float,
        robustness_score: float,
        simulation_cost: float,
    ) -> str:
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        trial_id = hashlib.sha256(
            f"{expression_id}\0{trial.profile}\0{json.dumps(trial.settings, sort_keys=True)}\0{created_at}".encode()
        ).hexdigest()
        with sqlite3.connect(database) as con:
            con.execute(
                """INSERT INTO settings_trials(trial_id,expression_id,setting_profile,parameter_delta_json,metrics_json,checks_json,quality_score,robustness_score,simulation_cost,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    trial_id,
                    expression_id,
                    trial.profile,
                    json.dumps(trial.parameter_delta, sort_keys=True),
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(checks, sort_keys=True),
                    quality_score,
                    robustness_score,
                    simulation_cost,
                    created_at,
                ),
            )
        return trial_id
