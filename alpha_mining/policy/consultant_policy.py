"""Configuration-only safety margins; platform limits remain dynamic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConsultantPolicy:
    gate_freshness_hours: float = 24.0
    quality_margin: float = 0.05
    turnover_margin: float = 0.01
    correlation_margin: float = 0.03
    correlation_ceiling: float = 0.65
    near_pass_ratio: float = 0.90
    min_correlation_overlap: int = 60
    max_candidates_per_hypothesis: int = 8
    max_behavior_per_round: int = 2
    max_parent_offspring: int = 8
    hard_parent_offspring: int = 12
    max_settings_trials: int = 6
    per_candidate_settings_budget: int = 6
    simulation_budget: int = 64
    execute_submit: bool = False
    confirmation_phrase: str = "I_UNDERSTAND_REAL_SUBMISSION"
    reward_weights: dict[str, float] = field(
        default_factory=lambda: {
            "platform_pass":    4.0,
            "quality_buffer":   2.0,
            "novelty":          1.5,
            "robustness":       1.5,
            "sub_universe_margin": 1.0,
            # Prod Corr signals — must dominate ordinary Sharpe/Fitness failures.
            # PASS is worth +8 (double platform_pass); FAIL is -10 (strong cluster suppression).
            # PENDING/MISSING/UNKNOWN give a small nudge toward obtaining a real observation.
            "prod_corr_pass":   8.0,
            "prod_corr_fail":  -10.0,
            "prod_corr_unknown": -2.0,
            "simulation_cost": -1.0,
            "duplicate":       -2.0,
            "unit_failure":    -2.0,
            "concentration":   -2.0,
        }
    )

    @classmethod
    def from_file(cls, path: str | Path | None) -> "ConsultantPolicy":
        if not path or not Path(path).is_file():
            return cls()
        try:
            import yaml

            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        section: dict[str, Any] = (
            raw.get("consultant", raw) if isinstance(raw, dict) else {}
        )
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in section.items() if key in allowed})

    def internal_limit(
        self, *, live_limit: float, direction: str, gate_name: str
    ) -> float:
        if "CORRELATION" in gate_name.upper():
            return min(
                float(live_limit) - self.correlation_margin, self.correlation_ceiling
            )
        if "TURNOVER" in gate_name.upper():
            return (
                float(live_limit) + self.turnover_margin
                if direction.upper() == "MIN"
                else float(live_limit) - self.turnover_margin
            )
        return (
            float(live_limit) + self.quality_margin
            if direction.upper() == "MIN"
            else float(live_limit) - self.quality_margin
        )
