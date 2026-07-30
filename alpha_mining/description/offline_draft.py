"""Fact-bound drafts for candidates that do not yet have a platform alpha id."""

from __future__ import annotations

from alpha_mining.offline.metadata import MetadataCache


def build_offline_description(
    *, expression: str, field_ids: list[str], metadata: MetadataCache,
    hypothesis: str, direction: str, windows: list[int | float],
    neutralization: str,
) -> str:
    fields = ", ".join(
        f"{field_id} ({metadata.fields[field_id].description or 'cached platform field'})"
        for field_id in field_ids
    )
    window_text = ", ".join(str(value) for value in windows) or "none"
    return (
        f"Data: {fields}. Signal construction: {expression}. "
        f"Logic: the candidate tests whether {hypothesis}. Time windows: {window_text}. "
        f"Direction: {direction}. Neutralization: {neutralization}. "
        "Risks and limitations: field coverage, timing, turnover, concentration, regime change, "
        "and platform correlation checks may invalidate the hypothesis. No performance result is asserted."
    )
