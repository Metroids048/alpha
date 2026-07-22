"""Shared immutable states for the description workflow."""

from __future__ import annotations

from enum import Enum


class DescriptionStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    PATCH_PENDING = "PATCH_PENDING"
    PATCHED = "PATCHED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    SCHEMA_UNKNOWN = "SCHEMA_UNKNOWN"


_TRANSITIONS: dict[DescriptionStatus, frozenset[DescriptionStatus]] = {
    DescriptionStatus.REQUIRED: frozenset(
        {DescriptionStatus.GENERATED, DescriptionStatus.SCHEMA_UNKNOWN, DescriptionStatus.FAILED}
    ),
    DescriptionStatus.GENERATED: frozenset(
        {DescriptionStatus.VALIDATED, DescriptionStatus.FAILED}
    ),
    DescriptionStatus.VALIDATED: frozenset(
        {DescriptionStatus.PATCH_PENDING, DescriptionStatus.FAILED}
    ),
    DescriptionStatus.PATCH_PENDING: frozenset(
        {DescriptionStatus.PATCHED, DescriptionStatus.VERIFIED, DescriptionStatus.FAILED}
    ),
    DescriptionStatus.PATCHED: frozenset(
        {DescriptionStatus.VERIFIED, DescriptionStatus.FAILED}
    ),
    DescriptionStatus.FAILED: frozenset(
        {DescriptionStatus.REQUIRED, DescriptionStatus.GENERATED}
    ),
    DescriptionStatus.SCHEMA_UNKNOWN: frozenset({DescriptionStatus.REQUIRED}),
    DescriptionStatus.NOT_REQUIRED: frozenset(),
    DescriptionStatus.VERIFIED: frozenset(),
}


def transition_description(
    current: DescriptionStatus, target: DescriptionStatus
) -> DescriptionStatus:
    if target is current:
        return current
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid description transition: {current.value} -> {target.value}")
    return target
