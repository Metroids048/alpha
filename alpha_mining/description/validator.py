"""Hard validation that prevents unsupported Description writes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .engine import DescriptionDraft
from .facts import DescriptionFacts
from .schema import DescriptionSchema


@dataclass(frozen=True)
class DescriptionValidation:
    valid: bool
    errors: tuple[str, ...]


def _get_path(payload: dict, path: tuple[str, ...]):
    value = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def validate_description(
    draft: DescriptionDraft,
    facts: DescriptionFacts,
    schema: DescriptionSchema,
) -> DescriptionValidation:
    errors: list[str] = []
    text = draft.text
    low = text.lower()
    if draft.alpha_type != facts.alpha_type or schema.alpha_type != facts.alpha_type:
        errors.append("ALPHA_TYPE_SCHEMA_MISMATCH")
    if draft.facts_hash != facts.facts_hash:
        errors.append("FACTS_HASH_MISMATCH")
    if draft.schema_hash != schema.schema_hash:
        errors.append("SCHEMA_HASH_MISMATCH")
    if set(schema.required_sections) - set(draft.sections):
        errors.append("REQUIRED_SECTIONS_MISSING")
    if _get_path(draft.payload, schema.payload_path) != text:
        errors.append("PAYLOAD_PATH_MISMATCH")
    if len(text) < schema.min_length or (
        schema.max_length is not None and len(text) > schema.max_length
    ):
        errors.append("LENGTH_INVALID")
    if any(field.lower() not in low for field in facts.fields):
        errors.append("FIELD_CONSISTENCY_FAILED")
    if any(operator.lower() not in low for operator in facts.operators):
        errors.append("OPERATOR_CONSISTENCY_FAILED")
    if any(str(window) not in text for window in facts.windows):
        errors.append("WINDOW_CONSISTENCY_FAILED")
    if any(group.lower() not in low for group in facts.groups):
        errors.append("GROUP_CONSISTENCY_FAILED")
    if facts.direction.lower() not in low:
        errors.append("DIRECTION_CONSISTENCY_FAILED")
    if any(str(value).lower() not in low for value in facts.settings.values()):
        errors.append("SETTINGS_CONSISTENCY_FAILED")
    if re.search(r"\b(?:sharpe|fitness|returns?|correlation)\b.{0,24}(?:=|is|of)\s*[+-]?\d", text, re.I):
        errors.append("UNSUPPORTED_PERFORMANCE_CLAIM")
    if re.search(r"\b(?:todo|tbd|placeholder|null|none)\b|\[[a-z_ ]+\]", text, re.I):
        errors.append("PLACEHOLDER_DETECTED")
    if len(text) < 80 or re.fullmatch(r"\s*this is a (?:good|strong) alpha signal\.?s*", text, re.I):
        errors.append("GENERIC_DESCRIPTION")
    return DescriptionValidation(not errors, tuple(dict.fromkeys(errors)))
