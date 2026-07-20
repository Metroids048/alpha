"""Canonical expression identities independent of the legacy monolith."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from alpha_mining.common import sig
from .operator_registry import BASE_VARS, FUNCTIONS, GROUPS, LITERALS

_NUMBER = re.compile(r"(?<![a-z_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", re.I)


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
