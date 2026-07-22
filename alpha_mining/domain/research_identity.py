"""Research identities intentionally exclude simulation settings."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def normalize_holding_horizon(window: int | float | str) -> str:
    value = int(float(window))
    for upper, label in (
        (5, "1-5"),
        (21, "6-21"),
        (63, "22-63"),
        (126, "64-126"),
        (252, "127-252"),
    ):
        if value <= upper:
            return label
    return ">252"


@dataclass(frozen=True)
class ResearchIdentity:
    economic_mechanism: str
    information_source: str
    information_timing: str
    comparison_basis: str
    holding_horizon: str
    risk_exposure: str

    def identity_id(self, settings: dict | None = None) -> str:
        del settings
        parts = (
            self.economic_mechanism,
            self.information_source,
            self.information_timing,
            self.comparison_basis,
            self.holding_horizon,
            self.risk_exposure,
        )
        canonical = "|".join(str(part).strip().lower() for part in parts)
        return "research_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
