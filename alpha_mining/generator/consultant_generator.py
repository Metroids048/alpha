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
        self, *, max_per_hypothesis: int = 8, max_same_behavior: int = 2
    ) -> None:
        self.max_per_hypothesis = min(7, max(1, int(max_per_hypothesis)))
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
        primary, secondary = (
            field_list[0],
            field_list[1] if len(field_list) > 1 else "close",
        )
        templates = [
            ("baseline", f"group_rank({primary}/cap, subindustry)-0.5"),
            ("level_to_change", f"group_rank(ts_delta({primary},63)/cap,subindustry)-0.5"),
            ("change_to_acceleration", f"rank(ts_delta(ts_delta({primary},63),21))"),
            ("absolute_to_historical_surprise", f"ts_zscore({primary},126)"),
            ("absolute_to_peer_relative", f"group_rank({primary},industry)-0.5"),
            ("regime_conditioned", f"trade_when(rank(volume/adv20)>0.5,group_rank({primary}/cap,subindustry)-0.5,-1)"),
            (
                "low_correlation_parent_hybrid",
                f"group_rank({primary}/cap,subindustry)-rank(ts_delta(close,5))",
            ),
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
