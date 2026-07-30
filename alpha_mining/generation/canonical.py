"""Canonical identities and structured expression facts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from alpha_mining.domain.expression_ast import AstNode, parse_expression
from alpha_mining.domain.expression_normalization import expression_identity
from alpha_mining.offline.metadata import MetadataCache


def canonical_skeleton(expression: str) -> str:
    """Collapse field, window, and cosmetic wrapper-only variants."""

    return expression_identity(expression).field_skeleton


def canonical_signature(
    expression: str,
    metadata: MetadataCache,
    *,
    generator_family: str,
    parent_template: str,
    neutralization: str,
    direction: str,
) -> dict[str, Any]:
    node = parse_expression(expression)
    operators: list[str] = []
    fields: list[str] = []
    windows: list[float | int] = []
    groupings: list[str] = []

    def walk(current: AstNode, parent_operator: str = "") -> None:
        if current.kind == "call":
            operators.append(current.value)
            if current.value.startswith("group_") and len(current.children) > 1:
                groupings.extend(_identifiers(current.children[1]))
            for child in current.children:
                walk(child, current.value)
            return
        if current.kind == "ident" and current.value in metadata.fields:
            fields.append(current.value)
        elif current.kind == "number" and parent_operator.startswith("ts_"):
            number = float(current.value)
            windows.append(int(number) if number.is_integer() else number)
        for child in current.children:
            walk(child, parent_operator)

    walk(node)
    identities = expression_identity(expression)
    unique_fields = sorted(set(fields))
    datasets = sorted({metadata.fields[field].dataset_id for field in unique_fields})
    return {
        "normalized_ast": node.as_dict(),
        "root_operator": node.value,
        "operator_multiset": dict(sorted(Counter(operators).items())),
        "fields": unique_fields,
        "datasets": datasets,
        "time_windows": windows,
        "grouping": sorted(set(groupings)),
        "neutralization": neutralization,
        "direction": direction,
        "generator_family": generator_family,
        "parent_template": parent_template,
        "exact_hash": identities.exact_hash,
        "parameter_skeleton": identities.parameter_skeleton,
        "skeleton": identities.field_skeleton,
    }


def _identifiers(node: AstNode) -> list[str]:
    values = [node.value] if node.kind == "ident" else []
    for child in node.children:
        values.extend(_identifiers(child))
    return values
