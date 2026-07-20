"""Behavior-first mutation admission policy."""

from __future__ import annotations

from dataclasses import dataclass
import math

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    extract_fields,
    extract_functions,
)


@dataclass(frozen=True)
class MutationDecision:
    allowed: bool
    reason: str


class MutationPolicy:
    def assess(
        self,
        parent: str,
        child: str,
        *,
        parent_settings: dict | None = None,
        child_settings: dict | None = None,
    ) -> MutationDecision:
        if not child.strip() or child.strip() == parent.strip():
            return MutationDecision(False, "IDENTICAL")
        if behavior_signature(parent, settings=parent_settings) == behavior_signature(
            child, settings=child_settings
        ):
            return MutationDecision(False, "BEHAVIOR_EQUIVALENT")
        if set(extract_fields(parent)) == set(
            extract_fields(child)
        ) and extract_functions(parent) == extract_functions(child):
            return MutationDecision(False, "PARAMETER_ONLY")
        return MutationDecision(True, "BEHAVIOR_DIFFERENT")


class GenerationQuota:
    """Round-wide quota enforcing behavior and field/topology concentration."""

    def __init__(
        self,
        *,
        round_capacity: int,
        max_behavior: int = 2,
        topology_share: float = 0.10,
    ) -> None:
        self.max_behavior = max(1, int(max_behavior))
        self.max_topology = max(
            1, math.floor(max(1, round_capacity) * float(topology_share))
        )
        self.behaviors: dict[str, int] = {}
        self.topologies: dict[str, int] = {}

    def admit(self, behavior: str, field_topology: str) -> MutationDecision:
        if self.behaviors.get(behavior, 0) >= self.max_behavior:
            return MutationDecision(False, "BEHAVIOR_QUOTA")
        if self.topologies.get(field_topology, 0) >= self.max_topology:
            return MutationDecision(False, "FIELD_TOPOLOGY_QUOTA")
        self.behaviors[behavior] = self.behaviors.get(behavior, 0) + 1
        self.topologies[field_topology] = self.topologies.get(field_topology, 0) + 1
        return MutationDecision(True, "QUOTA_AVAILABLE")
