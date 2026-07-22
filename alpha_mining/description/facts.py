"""Deterministic fact extraction for Description generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from alpha_mining.domain.expression_ast import AstNode, parse_expression
from alpha_mining.domain.expression_normalization import extract_functions
from alpha_mining.domain.operator_registry import FUNCTIONS, GROUPS, LITERALS


class DescriptionFactError(ValueError):
    pass


def _walk(node: AstNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _windows(node: AstNode) -> tuple[int, ...]:
    values: list[int] = []
    for item in _walk(node):
        if item.kind != "call" or not item.value.startswith("ts_"):
            continue
        for child in item.children[1:]:
            if child.kind == "number":
                value = int(float(child.value))
                if value not in values:
                    values.append(value)
                break
    return tuple(values)


@dataclass(frozen=True)
class DescriptionFacts:
    alpha_type: str
    expression: str
    ast: dict[str, Any]
    fields: tuple[str, ...]
    field_metadata: dict[str, dict[str, Any]]
    operators: tuple[str, ...]
    operator_definitions: dict[str, str]
    windows: tuple[int, ...]
    groups: tuple[str, ...]
    direction: str
    settings: dict[str, Any]
    hypothesis: dict[str, Any]
    facts_hash: str


def extract_description_facts(
    *,
    alpha_type: str,
    expression: str,
    field_metadata: Mapping[str, Mapping[str, Any]],
    operator_definitions: Mapping[str, str],
    hypothesis: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> DescriptionFacts:
    node = parse_expression(expression)
    operators = tuple(dict.fromkeys(extract_functions(expression)))
    unsupported_operators = sorted(
        operator for operator in operators if operator not in operator_definitions
    )
    if unsupported_operators:
        raise DescriptionFactError(
            f"unsupported operators: {', '.join(unsupported_operators)}"
        )
    identifiers = tuple(
        dict.fromkeys(item.value for item in _walk(node) if item.kind == "ident")
    )
    fields = tuple(
        value
        for value in identifiers
        if value not in FUNCTIONS and value not in GROUPS and value not in LITERALS
    )
    metadata = {str(key): dict(value) for key, value in field_metadata.items()}
    unsupported_fields = sorted(field for field in fields if field not in metadata)
    if unsupported_fields:
        raise DescriptionFactError(f"unsupported fields: {', '.join(unsupported_fields)}")
    groups = tuple(value for value in identifiers if value in GROUPS)
    direction = str(hypothesis.get("expected_direction") or "").strip()
    if not direction:
        raise DescriptionFactError("expected direction is required")
    kind = str(alpha_type or "").upper().strip()
    payload = {
        "alpha_type": kind,
        "expression": expression,
        "ast": node.as_dict(),
        "fields": fields,
        "field_metadata": metadata,
        "operators": operators,
        "operator_definitions": dict(operator_definitions),
        "windows": _windows(node),
        "groups": groups,
        "direction": direction,
        "settings": dict(settings),
        "hypothesis": dict(hypothesis),
    }
    facts_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return DescriptionFacts(**payload, facts_hash=facts_hash)
