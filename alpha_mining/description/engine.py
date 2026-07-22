"""Schema-directed deterministic Description drafting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .facts import DescriptionFacts
from .models import DescriptionStatus
from .schema import DescriptionSchema


@dataclass(frozen=True)
class DescriptionDraft:
    alpha_type: str
    sections: dict[str, str]
    text: str
    payload: dict[str, Any]
    source: str
    facts_hash: str
    schema_hash: str
    status: DescriptionStatus = DescriptionStatus.GENERATED


def _set_path(path: tuple[str, ...], value: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    cursor = root
    for key in path[:-1]:
        child: dict[str, Any] = {}
        cursor[key] = child
        cursor = child
    cursor[path[-1]] = value
    return root


def _field_text(facts: DescriptionFacts) -> str:
    return ", ".join(
        f"{field} ({facts.field_metadata[field].get('description') or 'platform metadata field'})"
        for field in facts.fields
    )


def _settings_text(facts: DescriptionFacts) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(facts.settings.items()))


def _known_sections(facts: DescriptionFacts) -> dict[str, str]:
    mechanism = str(facts.hypothesis.get("mechanism") or "the recorded mechanism")
    fields = _field_text(facts)
    operators = ", ".join(
        f"{name} ({facts.operator_definitions[name]})" for name in facts.operators
    )
    windows = ", ".join(str(value) for value in facts.windows) or "no time-series window"
    groups = ", ".join(facts.groups) or "no explicit grouping operator"
    settings = _settings_text(facts)
    if facts.alpha_type == "REGULAR":
        return {
            "hypothesis": f"The alpha tests whether {mechanism} predicts a relative cross-sectional response.",
            "data_rationale": f"The implemented expression uses {fields}; each field is identified by platform metadata.",
            "signal_construction": f"The expression applies {operators}, uses windows {windows}, and groups by {groups}.",
            "long_short_interpretation": f"The recorded direction is {facts.direction}; opposite signal values receive the opposite exposure.",
            "settings_rationale": f"The recorded simulation settings are {settings}; no unrecorded setting is assumed.",
            "risks_and_limitations": "Coverage gaps, reporting timing, turnover, concentration, regime change, and platform correlation checks can invalidate the hypothesis.",
        }
    if facts.alpha_type == "SELECTION":
        return {
            "selection_objective": f"The selection tests {mechanism} within the recorded simulation universe.",
            "selection_conditions": f"Selection conditions are exactly those in the expression using {operators}, windows {windows}, and group {groups}.",
            "economic_rationale": f"The selected inputs are {fields}, as identified by platform metadata.",
            "post_selection_signal": f"After selection, positions follow the recorded direction {facts.direction} with settings {settings}.",
            "risks_and_limitations": "Coverage, unstable membership, concentration, and platform correlation checks can block use.",
        }
    if facts.alpha_type == "COMBO":
        return {
            "component_rationale": f"The recorded components use {fields} to test {mechanism}.",
            "combination_method": f"The expression combines components with {operators}, windows {windows}, and group {groups}.",
            "incremental_information": "Incremental information is a hypothesis to be tested; no performance or correlation result is asserted.",
            "overlap_correlation_control_rationale": f"Overlap is controlled by platform SELF and PROD correlation checks; direction is {facts.direction} and settings are {settings}.",
            "risks_and_limitations": "Component overlap, unstable weights, coverage gaps, and unverified correlation can invalidate the combination.",
        }
    return {}


def build_deterministic_description(
    facts: DescriptionFacts, schema: DescriptionSchema
) -> DescriptionDraft:
    if schema.alpha_type != facts.alpha_type:
        raise ValueError("alpha type and schema do not match")
    sections = _known_sections(facts)
    if not sections:
        summary = (
            f"The recorded {facts.alpha_type} alpha tests {facts.hypothesis.get('mechanism')}; "
            f"it uses {_field_text(facts)}, operators {', '.join(facts.operators)}, "
            f"windows {list(facts.windows)}, groups {list(facts.groups)}, direction {facts.direction}, "
            f"and settings {_settings_text(facts)}. No performance result is asserted."
        )
        sections = {name: summary for name in schema.required_sections}
    missing = set(schema.required_sections) - set(sections)
    if missing:
        raise ValueError(f"renderer does not support required sections: {sorted(missing)}")
    text = "\n".join(
        f"{name.replace('_', ' ').title()}: {sections[name]}"
        for name in schema.required_sections
    )
    return DescriptionDraft(
        alpha_type=facts.alpha_type,
        sections=sections,
        text=text,
        payload=_set_path(schema.payload_path, text),
        source="deterministic",
        facts_hash=facts.facts_hash,
        schema_hash=schema.schema_hash,
    )
