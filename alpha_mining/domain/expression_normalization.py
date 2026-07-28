"""Canonical expression identities independent of the legacy monolith."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from alpha_mining.common import sig
from .operator_registry import BASE_VARS, FUNCTIONS, GROUPS, LITERALS

_NUMBER = re.compile(r"(?<![a-z_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", re.I)


@dataclass(frozen=True)
class ExpressionIdentity:
    """Stable identities used to reject semantically trivial re-simulations."""

    exact_hash: str
    parameter_skeleton: str
    field_skeleton: str


def extract_functions(expression: str) -> list[str]:
    return re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", str(expression or "").lower())


def extract_identifiers(expression: str) -> list[str]:
    return re.findall(r"\b[a-z_][a-z0-9_]*\b", str(expression or "").lower())


def extract_fields(expression: str) -> list[str]:
    excluded = FUNCTIONS | GROUPS | BASE_VARS | LITERALS
    return list(
        dict.fromkeys(
            token for token in extract_identifiers(expression) if token not in excluded
        )
    )


def exact_hash(expression: str) -> str:
    return hashlib.sha256(sig(expression).encode("utf-8")).hexdigest()


def normalized_expression(expression: str) -> str:
    text = sig(expression).lower()
    text = _NUMBER.sub("#", text)
    return re.sub(r"\s+", "", text)


def _compact_expression(expression: str) -> str:
    return re.sub(r"\s+", "", sig(expression).lower())


def _top_level_parts(text: str, operators: str) -> list[str] | None:
    depth = 0
    parts: list[str] = []
    start = 0
    for index, char in enumerate(text):
        depth += 1 if char == "(" else -1 if char == ")" else 0
        if depth == 0 and char in operators:
            if char in "+-" and index == 0:
                continue
            parts.append(text[start:index])
            start = index + 1
    if not parts:
        return None
    parts.append(text[start:])
    return parts


def _function_parts(text: str) -> tuple[str, list[str]] | None:
    match = re.fullmatch(r"([a-z_][a-z0-9_]*)\((.*)\)", text)
    if not match:
        return None
    name, body = match.groups()
    depth = 0
    args: list[str] = []
    start = 0
    for index, char in enumerate(body):
        depth += 1 if char == "(" else -1 if char == ")" else 0
        if depth == 0 and char == ",":
            args.append(body[start:index])
            start = index + 1
    args.append(body[start:])
    return name, args


def _skeleton(expression: str, *, neutralize_fields: bool) -> str:
    text = _strip_outer(_compact_expression(expression))
    if not text:
        return ""
    if text.startswith("-"):
        return f"neg({_skeleton(text[1:], neutralize_fields=neutralize_fields)})"
    for operators, label in (("+", "add"), ("*", "mul"), ("/", "div"), ("-", "sub")):
        parts = _top_level_parts(text, operators)
        if parts:
            if label == "add":
                parts = [part for part in parts if not _is_number(part, 0.0)]
            elif label == "mul":
                parts = [part for part in parts if not _is_number(part, 1.0)]
            if not parts:
                return "#"
            children = [_skeleton(part, neutralize_fields=neutralize_fields) for part in parts]
            if len(children) == 1 and label in {"add", "mul"}:
                return children[0]
            if label in {"add", "mul"}:
                children.sort()
            return f"{label}({','.join(children)})"
    function = _function_parts(text)
    if function:
        name, args = function
        children = [_skeleton(arg, neutralize_fields=neutralize_fields) for arg in args]
        if name in {"rank", "zscore", "normalize"} and len(children) == 1:
            return children[0]
        return f"{name}({','.join(children)})"
    if _NUMBER.fullmatch(text):
        return "#"
    if neutralize_fields and text not in GROUPS and text not in LITERALS:
        return "FIELD"
    return text


def _is_number(text: str, expected: float) -> bool:
    try:
        return float(text) == expected
    except ValueError:
        return False


def expression_identity(expression: str) -> ExpressionIdentity:
    """Return exact, parameter-neutral, and field-neutral identities.

    Skeletons retain operators, neutralization, and grouping controls, while
    collapsing only parameter, field, commutative-order, and wrapper noise.
    """

    canonical = _compact_expression(expression)
    return ExpressionIdentity(
        exact_hash=exact_hash(expression),
        parameter_skeleton=_skeleton(canonical, neutralize_fields=False),
        field_skeleton=_skeleton(canonical, neutralize_fields=True),
    )


def _strip_outer(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        complete = True
        for index, char in enumerate(text):
            depth += 1 if char == "(" else -1 if char == ")" else 0
            if depth == 0 and index != len(text) - 1:
                complete = False
                break
        if not complete or depth:
            break
        text = text[1:-1].strip()
    return text


def operator_topology(expression: str) -> str:
    text = normalized_expression(expression)
    reserved = FUNCTIONS | GROUPS | BASE_VARS
    text = re.sub(
        r"\b[a-z_][a-z0-9_]*\b",
        lambda m: m.group(0) if m.group(0) in reserved else "field",
        text,
    )
    return text


def _behavior_topology(expression: str) -> str:
    text = _strip_outer(operator_topology(expression))
    # Whole-expression sign and scalar multiplication do not change behavior risk.
    for _ in range(4):
        changed = False
        if text.startswith("-(") and text.endswith(")"):
            text = _strip_outer(text[1:])
            changed = True
        elif text.startswith("-"):
            text = _strip_outer(text[1:])
            changed = True
        scalar = re.match(r"^-?#\*\((.*)\)$", text)
        if scalar:
            text = _strip_outer(scalar.group(1))
            changed = True
        scalar = re.match(r"^\((.*)\)\*-?#$", text)
        if scalar:
            text = _strip_outer(scalar.group(1))
            changed = True
        if not changed:
            break
    text = re.sub(r"(?<=\))[-+]#(?=[,)]|$)", "", text)
    text = re.sub(r"(?<=\w)[-+]#(?=[,)]|$)", "", text)
    # The numeric canonicalizer consumes the sign in ``-0.5``; a number
    # directly following a completed signal node is therefore a centering
    # constant rather than a function argument.
    text = re.sub(r"(?<=\))#(?=[,)]|$)", "", text)
    text = re.sub(r"\*-?#", "", text)
    text = text.replace("-rank(", "rank(").replace("+-", "+").replace("--", "")
    return _strip_outer(text)


def behavior_signature(
    expression: str, *, settings: dict[str, Any] | None = None
) -> str:
    del settings
    if not sig(expression):
        return ""
    fields = "|".join(sorted(extract_fields(expression))[:8]) or "-"
    return f"{fields}::{_behavior_topology(expression)}"


def structure_signature(expression: str) -> str:
    functions = ">".join(extract_functions(expression)[:12]) or "raw"
    fields = "|".join(sorted(extract_fields(expression))[:8]) or "-"
    return f"{functions}::{fields}::{operator_topology(expression)}"


# Legacy-compatible names.
_normalized_expression = normalized_expression
_structure_signature = structure_signature
_behavior_signature = behavior_signature
