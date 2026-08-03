"""Deterministic strategy families assembled only from cached metadata."""

from __future__ import annotations

from dataclasses import dataclass

from alpha_mining.offline.metadata import MetadataCache


@dataclass(frozen=True)
class GeneratedExpression:
    expression: str
    family: str
    parent_template: str
    hypothesis: str
    direction: str


_FAMILY_SPECS = (
    ("momentum", ("price", "expectation", "event"), "persistent changes continue over the selected horizon", "higher signal values are long"),
    ("reversion", ("price",), "extreme recent moves partially reverse", "higher recent moves are short"),
    ("volatility", ("volatility", "price"), "changes in realized risk identify a relative risk regime", "lower relative risk is long"),
    ("liquidity", ("liquidity",), "persistent trading activity carries information about relative demand", "higher persistent activity is long"),
    ("price_volume_divergence", ("price", "liquidity"), "price and trading activity divergence identifies imbalanced demand", "positive divergence is long"),
    ("fundamental_change", ("fundamental",), "changes in operating fundamentals are incorporated gradually", "improving fundamentals are long"),
    ("valuation", ("valuation",), "relative valuation levels mean-revert toward fundamentals", "higher valuation yield is long"),
    ("quality", ("quality",), "persistent operating quality supports relative value", "higher quality is long"),
    ("expectation_revision", ("expectation",), "analyst expectation revisions diffuse gradually", "positive revisions are long"),
    ("event_drift", ("event",), "event information is incorporated over multiple sessions", "positive event signals are long"),
    ("residual", ("price", "volatility"), "short-horizon change relative to its slower component isolates residual movement", "positive residual movement is long"),
    ("conditional_regime", ("volatility", "liquidity", "price"), "signal persistence varies with its recent regime", "higher regime-adjusted signal is long"),
)


def generate_candidate_pool(metadata: MetadataCache) -> list[GeneratedExpression]:
    available = set(metadata.operators)
    required = {"rank", "add", "subtract", "multiply", "divide"}
    unary_time = [
        name for name in (
            "ts_delta", "ts_mean", "ts_rank", "ts_std_dev", "ts_zscore",
            "ts_sum", "ts_min", "ts_max", "ts_decay_linear",
        ) if name in available
    ]
    if not required.issubset(available) or len(unary_time) < 4:
        return []

    by_category: dict[str, list[str]] = {}
    for field in metadata.fields.values():
        by_category.setdefault(field.category, []).append(field.field_id)
    fallback = sorted(metadata.fields)
    pool: list[GeneratedExpression] = []

    for family_index, (family, categories, hypothesis, direction) in enumerate(_FAMILY_SPECS):
        selected = [field for category in categories for field in sorted(by_category.get(category, []))]
        if not selected:
            continue
        primary = selected[0]
        secondary = selected[1] if len(selected) > 1 else fallback[(family_index + 1) % len(fallback)]
        # Vary both the base shape and bounded nesting depth so a long-running
        # local queue can continue exploring structurally distinct candidates.
        for variant in range(65):
            first = unary_time[(family_index + variant) % len(unary_time)]
            second = unary_time[(family_index * 2 + variant + 1) % len(unary_time)]
            third = unary_time[(family_index + variant * 3 + 2) % len(unary_time)]
            w1 = (5, 10, 21, 42, 63, 126)[variant % 6]
            w2 = (10, 20, 42, 63, 126, 252)[(family_index + variant) % 6]
            left = f"{first}({primary},{w1})"
            right = f"{second}({secondary},{w2})"
            mode = variant % 6
            if mode == 0:
                body = f"subtract({left},{right})"
            elif mode == 1:
                body = f"divide({left},add(abs({right}),1))" if "abs" in available else f"divide({left},add({right},1))"
            elif mode == 2:
                body = f"multiply({left},{right})"
            elif mode == 3:
                body = f"{third}(subtract({left},{right}),{w1})"
            elif mode == 4:
                body = f"subtract({third}({left},{w2}),{right})"
            else:
                body = f"divide({third}({left},{w1}),add(abs({right}),1))" if "abs" in available else f"divide({third}({left},{w1}),add({right},1))"
            body = _family_transform(body, family_index, w1, available, unary_time)
            for depth in range(variant // 13):
                wrapper = unary_time[(family_index + variant + depth) % len(unary_time)]
                body = f"{wrapper}({body},{w2})"
            expression = f"rank({body})"
            pool.append(
                GeneratedExpression(
                    expression=expression,
                    family=family,
                    parent_template=f"{family}_structure_{variant + 1:02d}",
                    hypothesis=hypothesis,
                    direction=direction,
                )
            )
    return pool


def _family_transform(
    body: str,
    family_index: int,
    window: int,
    available: set[str],
    unary_time: list[str],
) -> str:
    """Make the family mechanism part of the topology, not merely its field choice."""

    transforms = (
        lambda value: value,
        lambda value: f"subtract(0,{value})",
        lambda value: f"ts_mean({value},{window})" if "ts_mean" in available else value,
        lambda value: f"ts_delta({value},{window})" if "ts_delta" in available else value,
        lambda value: f"ts_rank({value},{window})" if "ts_rank" in available else value,
        lambda value: f"ts_zscore({value},{window})" if "ts_zscore" in available else value,
        lambda value: f"ts_std_dev({value},{window})" if "ts_std_dev" in available else value,
        lambda value: f"ts_sum({value},{window})" if "ts_sum" in available else value,
        lambda value: (
            f"subtract(ts_max({value},{window}),ts_min({value},{window}))"
            if {"ts_max", "ts_min"}.issubset(available) else value
        ),
        lambda value: (
            f"ts_decay_linear({value},{window})"
            if "ts_decay_linear" in available else value
        ),
        lambda value: (
            f"subtract({value},ts_mean({value},{window}))"
            if "ts_mean" in available else value
        ),
        lambda value: (
            f"divide({value},add(abs(ts_std_dev({value},{window})),1))"
            if {"abs", "ts_std_dev"}.issubset(available) else value
        ),
    )
    transformed = transforms[family_index % len(transforms)](body)
    if transformed != body or family_index == 0:
        return transformed
    # Sparse caches may not contain a preferred transform. A repeated available
    # time-series operation still represents a distinct multi-horizon mechanism.
    operator = unary_time[family_index % len(unary_time)]
    for _ in range(1 + family_index // len(unary_time)):
        transformed = f"{operator}({transformed},{window})"
    return transformed
