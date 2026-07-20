"""Level-1 structural correlation fingerprints."""

from __future__ import annotations

import re
from dataclasses import dataclass

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    exact_hash,
    extract_fields,
    extract_functions,
    normalized_expression,
    operator_topology,
)
from alpha_mining.legacy.features import field_category


@dataclass(frozen=True)
class Fingerprint:
    exact_hash: str
    normalized: str
    behavior: str
    operators: frozenset[str]
    field_families: frozenset[str]
    window_bins: frozenset[str]
    topology: str


def fingerprint(expression: str) -> Fingerprint:
    windows = set()
    for raw in re.findall(r"\b\d+\b", expression):
        value = int(raw)
        windows.add(
            "short"
            if value <= 20
            else "medium"
            if value <= 63
            else "long"
            if value <= 252
            else "very_long"
        )
    return Fingerprint(
        exact_hash(expression),
        normalized_expression(expression),
        behavior_signature(expression),
        frozenset(extract_functions(expression)),
        frozenset(field_category(f) for f in extract_fields(expression)),
        frozenset(windows),
        operator_topology(expression),
    )


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left | right else 1.0


def compare_fingerprints(
    left: Fingerprint, right: Fingerprint
) -> dict[str, float | bool]:
    topology_left = set(re.findall(r"[a-z_]+|#", left.topology))
    topology_right = set(re.findall(r"[a-z_]+|#", right.topology))
    return {
        "exact_identity": left.exact_hash == right.exact_hash,
        "normalized_identity": left.normalized == right.normalized,
        "behavior_identity": left.behavior == right.behavior,
        "operator_jaccard": jaccard(left.operators, right.operators),
        "field_family_similarity": jaccard(left.field_families, right.field_families),
        "window_bin_similarity": jaccard(left.window_bins, right.window_bins),
        "topology_similarity": len(topology_left & topology_right)
        / len(topology_left | topology_right)
        if topology_left | topology_right
        else 1.0,
    }
