"""Expression feature extraction for behavioral deduplication."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from alpha_mining.domain.expression_ast import (
    ExpressionSyntaxError,
    depth,
    parse_expression,
)
from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    exact_hash,
    extract_fields,
    extract_functions,
    normalized_expression,
    operator_topology,
    structure_signature,
)


def _window_bin(value: int) -> str:
    for upper, label in (
        (5, "1-5"),
        (20, "6-20"),
        (63, "21-63"),
        (126, "64-126"),
        (252, "127-252"),
    ):
        if value <= upper:
            return label
    return "253+"


def field_category(field: str) -> str:
    low = field.lower()
    if any(
        x in low
        for x in ("sales", "revenue", "income", "asset", "debt", "cash", "eps", "ebit")
    ):
        return "fundamental"
    if any(x in low for x in ("analyst", "estimate", "forecast", "revision", "rating")):
        return "analyst"
    if any(x in low for x in ("news", "sentiment", "social", "event")):
        return "news_sentiment"
    if any(
        x in low for x in ("close", "open", "volume", "vwap", "price", "return", "adv")
    ):
        return "price_volume"
    return "other"


@dataclass(frozen=True)
class ExpressionFeatures:
    exact_hash: str
    normalized_expression: str
    ast_json: str
    structure_signature: str
    behavior_signature: str
    operators: tuple[str, ...]
    topology: str
    fields: tuple[str, ...]
    field_categories: tuple[str, ...]
    windows: tuple[str, ...]
    grouping: tuple[str, ...]
    normalizers: tuple[str, ...]
    conditions: tuple[str, ...]
    nesting_depth: int
    operator_count: int
    unit_warnings: tuple[str, ...]
    parse_valid: bool


def extract_features(expression: str) -> ExpressionFeatures:
    operators = tuple(extract_functions(expression))
    fields = tuple(extract_fields(expression))
    groups = tuple(
        sorted(
            set(
                re.findall(
                    r"\b(?:market|sector|industry|subindustry|country)\b",
                    expression.lower(),
                )
            )
        )
    )
    windows = tuple(
        _window_bin(int(value)) for value in re.findall(r"\b(\d{1,4})\b", expression)
    )
    normalizers = tuple(
        op
        for op in operators
        if op
        in {
            "rank",
            "zscore",
            "normalize",
            "winsorize",
            "group_rank",
            "group_zscore",
            "divide",
        }
    )
    conditions = tuple(
        op for op in operators if op in {"trade_when", "if_else", "bucket"}
    )
    warnings: list[str] = []
    if re.search(r"\b(?:adv20|volume)\s*[+]\s*\d", expression.lower()):
        warnings.append("UNIT_DIMENSIONED_ADDITION")
    try:
        node = parse_expression(expression)
        ast_json, nesting, valid = node.to_json(), depth(node), True
    except ExpressionSyntaxError as exc:
        ast_json, nesting, valid = json.dumps({"error": str(exc)}), 0, False
    return ExpressionFeatures(
        exact_hash(expression),
        normalized_expression(expression),
        ast_json,
        structure_signature(expression),
        behavior_signature(expression),
        operators,
        operator_topology(expression),
        fields,
        tuple(sorted({field_category(f) for f in fields})),
        windows,
        groups,
        normalizers,
        conditions,
        nesting,
        len(operators),
        tuple(warnings),
        valid,
    )


def feature_distance(left: dict, right: dict) -> float:
    def tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z_]+|\d+", str(value).lower()))

    a, b = (
        tokens(left.get("structure_signature", "")),
        tokens(right.get("structure_signature", "")),
    )
    jaccard = len(a & b) / len(a | b) if a | b else 1.0
    return 1.0 - jaccard
