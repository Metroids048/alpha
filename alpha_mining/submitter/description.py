"""Offline WorldQuant alpha-description drafting with a deterministic fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


MIN_DESCRIPTION_LENGTH = 100


class StructuredLLM(Protocol):
    def generate_json(
        self, *, system_prompt: str, user_prompt: str, json_schema: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DescriptionDraft:
    text: str
    source: str


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
