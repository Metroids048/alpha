"""Evidence-gated knowledge rules and public-expression crowding guard."""

from __future__ import annotations

from dataclasses import dataclass

from alpha_mining.domain.expression_normalization import behavior_signature, exact_hash


@dataclass(frozen=True)
class KnowledgeRule:
    source_type: str
    source_tier: str
    mechanism: str
    approved: bool = False
    platform_validation_status: str = "MISSING"

    @property
    def production_eligible(self) -> bool:
        return self.approved and self.platform_validation_status.upper() == "PASS"


class PublicExpressionGuard:
    def __init__(self, expressions: list[str]) -> None:
        self._exact = {exact_hash(value) for value in expressions if value.strip()}
        self._behavior = {behavior_signature(value) for value in expressions if value.strip()}

    def allows(self, candidate: str) -> bool:
        return exact_hash(candidate) not in self._exact and behavior_signature(candidate) not in self._behavior

