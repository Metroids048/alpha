"""Offline WorldQuant alpha-description drafting with a deterministic fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from alpha_mining.domain.expression_normalization import extract_fields, extract_functions
from alpha_mining.domain.operator_registry import FUNCTIONS, GROUPS, LITERALS


MIN_DESCRIPTION_LENGTH = 100


class StructuredLLM(Protocol):
    def generate_json(
        self, *, system_prompt: str, user_prompt: str, json_schema: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DescriptionDraft:
    text: str
    source: str


@dataclass(frozen=True)
class TypedDescriptionDraft:
    alpha_type: str
    sections: dict[str, str]
    patch_payload: dict[str, dict[str, str]]
    fields: tuple[str, ...]
    operators: tuple[str, ...]
    windows: tuple[int, ...]
    expected_direction: str


@dataclass(frozen=True)
class DescriptionValidation:
    valid: bool
    errors: tuple[str, ...]


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _render(parts: dict[str, object]) -> str | None:
    labels = (
        ("Idea", "idea"),
        ("Rationale for data used", "data_rationale"),
        ("Rationale for operators used", "operator_rationale"),
    )
    rendered = []
    for label, key in labels:
        value = _clean(parts.get(key))
        if not value:
            return None
        rendered.append(f"{label}: {value}")
    text = "\n".join(rendered)
    return text if len(text) >= MIN_DESCRIPTION_LENGTH else None


def _identifiers(expression: str) -> list[str]:
    ignored = {
        "add",
        "sub",
        "multiply",
        "divide",
        "rank",
        "zscore",
        "market",
        "group_neutralize",
        "group_rank",
        "winsorize",
        "ts_rank",
        "ts_zscore",
    }
    values = [
        token
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*\b", expression)
        if token.lower() not in ignored
    ]
    return list(dict.fromkeys(values))[:4]


def _template(expression: str, family: str) -> DescriptionDraft:
    fields = ", ".join(_identifiers(expression)) or "the selected market data fields"
    family_text = _clean(family) or "cross-sectional"
    text = (
        f"Idea: This {family_text} alpha evaluates persistent relative differences across liquid securities rather than a single market event.\n"
        f"Rationale for data used: It uses {fields} because these inputs provide measurable information for comparing companies or market behavior consistently.\n"
        "Rationale for operators used: Time-series ranking, normalization, and neutralization reduce transient noise and preserve a diversified cross-sectional signal."
    )
    if len(text) < MIN_DESCRIPTION_LENGTH:
        text += " The construction is intended for systematic research and repeated validation."
    return DescriptionDraft(text=text, source="template")


def generate_description(
    expression: str,
    *,
    llm: StructuredLLM | None = None,
    family: str = "",
    source: str = "",
) -> DescriptionDraft:
    """Return a three-part English description without contacting WorldQuant."""
    expression = _clean(expression)
    if llm is not None:
        schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["idea", "data_rationale", "operator_rationale"],
            "properties": {
                "idea": {"type": "string"},
                "data_rationale": {"type": "string"},
                "operator_rationale": {"type": "string"},
            },
        }
        try:
            response = llm.generate_json(
                system_prompt="Write factual English WorldQuant alpha descriptions. Do not claim performance or use placeholders.",
                user_prompt=(
                    f"Expression: {expression}\nFamily: {_clean(family)}\nSource: {_clean(source)}\n"
                    "Return the three requested sections for a researcher-facing alpha description."
                ),
                json_schema=schema,
            )
            rendered = _render(dict(response))
            if rendered is not None:
                return DescriptionDraft(text=rendered, source="deepseek")
        except Exception:
            pass
    return _template(expression, family)


def _windows(expression: str) -> tuple[int, ...]:
    values = re.findall(r"\bts_[a-z_]+\s*\([^,]+,\s*(\d+)\s*\)", expression, re.I)
    return tuple(dict.fromkeys(int(value) for value in values))


def _description_fields(expression: str) -> tuple[str, ...]:
    excluded = FUNCTIONS | GROUPS | LITERALS
    values = re.findall(r"\b[a-z_][a-z0-9_]*\b", expression.lower())
    return tuple(dict.fromkeys(value for value in values if value not in excluded))


def _render_sections(sections: dict[str, str]) -> str:
    return "\n".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in sections.items())


def build_description(
    *,
    alpha_type: str,
    expression: str,
    field_metadata: dict[str, dict[str, Any]],
    settings: dict[str, Any],
    hypothesis: dict[str, Any],
) -> TypedDescriptionDraft:
    """Build a factual, type-specific description without performance claims."""
    kind = str(alpha_type or "REGULAR").upper()
    if kind not in {"REGULAR", "SELECTION", "COMBO"}:
        raise ValueError(f"unsupported alpha type: {kind}")
    fields = _description_fields(expression)
    operators = tuple(extract_functions(expression))
    windows = _windows(expression)
    mechanism = _clean(hypothesis.get("mechanism")) or "the recorded research mechanism"
    direction = _clean(hypothesis.get("expected_direction"))
    field_text = ", ".join(
        f"{field} ({_clean((field_metadata.get(field) or {}).get('description')) or 'verified platform field'})"
        for field in fields
    ) or "verified fields from the expression"
    operator_text = ", ".join(operators) or "the expression topology"
    setting_text = ", ".join(f"{key}={value}" for key, value in sorted(settings.items()))
    if kind == "REGULAR":
        sections = {
            "hypothesis": f"The alpha tests whether {mechanism} predicts relative returns.",
            "data_rationale": f"The expression uses {field_text}.",
            "operator_rationale": f"The implemented operators are {operator_text}; windows are {list(windows)}.",
            "long_short_interpretation": f"The recorded direction is {direction or 'unverified'}; higher and lower signal values define opposite sides.",
            "settings_rationale": f"The simulation uses {setting_text or 'explicit platform defaults'}.",
            "expected_behavior": "Expected behavior is a testable cross-sectional response; no performance outcome is claimed.",
            "risks": "Field coverage, regime change, crowding, turnover, and correlation checks may invalidate the hypothesis.",
        }
    elif kind == "SELECTION":
        sections = {
            "selection_universe": f"Selection is evaluated in {settings.get('region', '*')}/{settings.get('universe', '*')}.",
            "selection_conditions": f"Conditions are defined only by the expression {expression}.",
            "economic_rationale": f"The selection tests {mechanism}.",
            "signal_construction": f"It uses {field_text} with {operator_text} and windows {list(windows)}.",
            "risks": "Sparse coverage, selection instability, crowding, and correlation checks may block use.",
        }
    else:
        sections = {
            "component_alphas": f"Components are those referenced by the combo expression: {expression}.",
            "combination_logic": f"The recorded operator topology is {operator_text}.",
            "incremental_rationale": f"The combination tests incremental evidence for {mechanism}.",
            "correlation_control": "Correlation control is accepted only after an explicit platform SELF_CORRELATION PASS.",
            "risks": "Component overlap, unstable weights, crowding, and missing platform evidence block use.",
        }
    slot = kind.lower()
    return TypedDescriptionDraft(kind, sections, {slot: {"description": _render_sections(sections)}}, fields, operators, windows, direction)


def validate_description(
    draft: TypedDescriptionDraft,
    *,
    expression: str,
    expected_direction: str | None = None,
) -> DescriptionValidation:
    errors: list[str] = []
    actual_fields = _description_fields(expression)
    if set(actual_fields) != set(draft.fields):
        errors.append("DESCRIPTION_FIELDS_MISMATCH")
    if _windows(expression) != draft.windows:
        errors.append("DESCRIPTION_WINDOWS_MISMATCH")
    if expected_direction is not None and _clean(expected_direction) != draft.expected_direction:
        errors.append("DESCRIPTION_DIRECTION_MISMATCH")
    if set(draft.patch_payload) != {draft.alpha_type.lower()}:
        errors.append("DESCRIPTION_TYPE_SLOT_MISMATCH")
    text = _render_sections(draft.sections)
    if re.search(r"\b(?:none|tbd|todo|null)\b", text, re.I):
        errors.append("DESCRIPTION_PLACEHOLDER")
    return DescriptionValidation(not errors, tuple(errors))
