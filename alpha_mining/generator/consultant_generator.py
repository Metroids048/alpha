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
    economic_rationale: str = ""
    knowledge_refs: tuple[str, ...] = ()
    expected_signal: str = ""
    expected_turnover_behavior: str = ""
    repair_origin: str = ""
    degraded: bool = False


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
        mechanism: str,
        horizon: str,
        fields: Iterable[str],
        parent_expression: str = "",
    ) -> list[ConsultantCandidate]:
        field_list = list(
            dict.fromkeys(str(field).strip() for field in fields if str(field).strip())
        )
        if not field_list:
            return []
        windows = {
            "short": (5, 10, 20),
            "medium": (21, 63, 126),
            "long": (63, 126, 252),
        }.get(str(horizon or "").strip().lower(), (21, 63, 126))

        keyword_groups = {
            "momentum": ("momentum", "trend", "growth"),
            "reversal": ("reversal", "mean reversion", "contrarian"),
            "volatility": ("volatility", "risk"),
            "fundamental": ("fundamental", "value", "quality", "profitability"),
        }

        def classify(text: str) -> str:
            normalized = " ".join(str(text or "").lower().replace("_", " ").split())
            for category, keywords in keyword_groups.items():
                if any(keyword in normalized for keyword in keywords):
                    return category
            return "balanced"

        profile = classify(mechanism)
        if profile == "balanced":
            profile = classify(family)
        window_order = {
            "momentum": (windows[0], windows[1], windows[2]),
            "reversal": (windows[0], windows[2], windows[1]),
            "volatility": (windows[1], windows[0], windows[2]),
            "fundamental": (windows[2], windows[1], windows[0]),
            "balanced": windows,
        }[profile]
        w_short, w_medium, w_long = window_order

        template_groups = {
            "momentum": [
                ("medium_horizon_momentum", lambda f, _o: f"rank(ts_rank({f},{w_medium}))"),
                ("smoothed_delta", lambda f, _o: f"rank(ts_mean(ts_delta({f},1),{w_short}))"),
                ("decayed_momentum", lambda f, _o: f"rank(ts_decay_linear(ts_delta({f},{w_short}),{w_medium}))"),
                ("information_ratio", lambda f, _o: f"rank(ts_ir({f},{w_medium}))"),
            ],
            "reversal": [
                ("short_horizon_reversal", lambda f, _o: f"-rank(ts_delta({f},{w_short}))"),
                ("cross_signal_divergence", lambda f, o: f"rank(ts_zscore({f},{w_medium})-ts_zscore({o},{w_medium}))"),
                ("rank_spread_horizons", lambda f, _o: f"rank(ts_rank({f},{w_short})-ts_rank({f},{w_long}))"),
            ],
            "volatility": [
                ("volatility_regime", lambda f, _o: f"-rank(ts_std_dev({f},{w_medium}))"),
                ("relative_flow", lambda f, _o: f"rank(ts_mean({f},{w_short})/ts_std_dev({f},{w_medium}))"),
                ("vol_ratio_regimes", lambda f, _o: f"rank(ts_std_dev({f},{w_short})/ts_std_dev({f},{w_long}))"),
                ("range_signal", lambda f, _o: f"rank(ts_max({f},{w_short})-ts_min({f},{w_short}))"),
            ],
            "fundamental": [
                ("change_to_acceleration", lambda f, _o: f"rank(ts_delta(ts_delta({f},{w_medium}),{w_short}))"),
                ("historical_surprise", lambda f, _o: f"rank(ts_zscore({f},{w_long}))"),
                ("normalized_level", lambda f, _o: f"rank({f}/ts_mean({f},{w_medium}))"),
            ],
        }
        category_order = ["momentum", "reversal", "volatility", "fundamental"]
        if profile != "balanced":
            category_order = [profile] + [item for item in category_order if item != profile]
        templates = []
        for category in category_order:
            templates.extend(template_groups[category])
        out: list[ConsultantCandidate] = []
        counts: dict[str, int] = {}
        for index, (mutation_type, builder) in enumerate(templates):
            field = field_list[index % len(field_list)]
            other = field_list[(index + 1) % len(field_list)]
            expression = builder(field, other)
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
