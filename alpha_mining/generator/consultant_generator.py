"""Bounded consultant candidate generation by research mechanism."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from alpha_mining.domain.expression_normalization import behavior_signature
from .mutation_policy import MutationPolicy


@dataclass(frozen=True)
class ConsultantCandidate:
    candidate_id: str
    hypothesis_id: str
    family: str
    mutation_type: str
    expression: str
    parent_id: str = ""


class ConsultantGenerator:
    def __init__(
        self, *, max_per_hypothesis: int = 14, max_same_behavior: int = 2
    ) -> None:
        self.max_per_hypothesis = min(14, max(1, int(max_per_hypothesis)))
        self.max_same_behavior = max(1, int(max_same_behavior))
        self.policy = MutationPolicy()

    def generate(
        self,
        *,
        hypothesis_id: str,
        family: str,
        fields: Iterable[str],
        parent_expression: str = "",
    ) -> list[ConsultantCandidate]:
        field_list = list(
            dict.fromkeys(str(field).strip() for field in fields if str(field).strip())
        )
        if not field_list:
            return []
        primary = field_list[0]
        templates = [
            # --- Original 7 templates (field_skeletons: ts_rank, neg(ts_delta),
            #     ts_delta(ts_delta), ts_zscore, neg(ts_std_dev),
            #     div(ts_mean,ts_std_dev), sub(ts_zscore,ts_zscore)) ---
            ("medium_horizon_momentum", f"rank(ts_rank({primary},63))"),
            ("short_horizon_reversal", f"-rank(ts_delta({primary},5))"),
            ("change_to_acceleration", f"rank(ts_delta(ts_delta({primary},63),21))"),
            ("historical_surprise", f"rank(ts_zscore({primary},126))"),
            ("volatility_regime", f"-rank(ts_std_dev({primary},63))"),
            ("relative_flow", f"rank(ts_mean({primary},21)/ts_std_dev({primary},63))"),
            (
                "cross_signal_divergence",
                f"rank(ts_zscore({primary},63)-ts_zscore({primary},21))",
            ),
            # --- Extension 7 templates (distinct field_skeletons) ---
            # skeleton: ts_mean(ts_delta(FIELD,#),#)
            ("smoothed_delta", f"rank(ts_mean(ts_delta({primary},1),20))"),
            # skeleton: div(ts_std_dev(FIELD,#),ts_std_dev(FIELD,#))
            ("vol_ratio_regimes", f"rank(ts_std_dev({primary},5)/ts_std_dev({primary},20))"),
            # skeleton: sub(ts_rank(FIELD,#),ts_rank(FIELD,#))
            ("rank_spread_horizons", f"rank(ts_rank({primary},20)-ts_rank({primary},60))"),
            # skeleton: div(FIELD,ts_mean(FIELD,#))
            ("normalized_level", f"rank({primary}/ts_mean({primary},20))"),
            # skeleton: ts_decay_linear(ts_delta(FIELD,#),#)
            ("decayed_momentum", f"rank(ts_decay_linear(ts_delta({primary},5),10))"),
            # skeleton: ts_ir(FIELD,#)
            ("information_ratio", f"rank(ts_ir({primary},20))"),
            # skeleton: sub(ts_max(FIELD,#),ts_min(FIELD,#))
            ("range_signal", f"rank(ts_max({primary},20)-ts_min({primary},20))"),
        ]
        out: list[ConsultantCandidate] = []
        counts: dict[str, int] = {}
        for mutation_type, expression in templates:
            signature = behavior_signature(expression)
            if counts.get(signature, 0) >= self.max_same_behavior:
                continue
            if (
                parent_expression
                and not self.policy.assess(parent_expression, expression).allowed
            ):
                continue
            candidate_id = (
                "candidate_"
                + hashlib.sha256(f"{hypothesis_id}\0{expression}".encode()).hexdigest()[
                    :20
                ]
            )
            out.append(
                ConsultantCandidate(
                    candidate_id, hypothesis_id, family, mutation_type, expression
                )
            )
            counts[signature] = counts.get(signature, 0) + 1
            if len(out) >= self.max_per_hypothesis:
                break
        return out
