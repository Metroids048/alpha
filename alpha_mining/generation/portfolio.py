"""Deterministic portfolio-level diversity selection for local candidates.

This module is deliberately pure: it reads candidate/inventory/feedback
snapshots and returns selection decisions. Queue persistence remains owned by
``CandidateCsvQueue``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    exact_hash,
    expression_identity,
    extract_fields,
    normalized_expression,
    operator_topology,
    structure_signature,
)


_ACTIVE_STATUS = "PENDING_SIMULATION"
_RISK_WEIGHTS = {
    "SELF_CORRELATION": 3.0,
    "LOW_SHARPE": 2.0,
    "HIGH_TURNOVER": 2.0,
}


@dataclass(frozen=True)
class PortfolioLimits:
    """Fixed limits for one cycle and the active pending queue."""

    cycle_field_skeleton_max: int = 1
    cycle_topology_max: int = 1
    cycle_parent_max: int = 1
    active_field_skeleton_max: int = 2
    active_topology_fraction: float = 0.25
    feedback_min_samples: int = 2
    policy_version: str = "portfolio-diversity-v1"


@dataclass(frozen=True)
class DiversityVector:
    dataset: str
    fields: tuple[str, ...]
    field_skeleton: str
    operator_topology: str
    strategy_family: str
    parent_template: str
    exact_hash: str
    normalized_hash: str
    structure_signature: str
    behavior_signature: str

    @classmethod
    def from_candidate(cls, candidate: Any) -> "DiversityVector":
        expression = str(getattr(candidate, "expression", "") or "").strip()
        identity = expression_identity(expression)
        datasets = getattr(candidate, "datasets", ())
        dataset = ",".join(sorted(str(item) for item in datasets if str(item)))
        parent = str(getattr(candidate, "parent_seed", "") or "")
        family = operator_topology(expression)
        return cls(
            dataset=dataset,
            fields=tuple(sorted(extract_fields(expression))),
            field_skeleton=identity.field_skeleton,
            operator_topology=family,
            strategy_family=family,
            parent_template=parent,
            exact_hash=identity.exact_hash,
            normalized_hash=hashlib.sha256(normalized_expression(expression).encode("utf-8")).hexdigest(),
            structure_signature=structure_signature(expression),
            behavior_signature=behavior_signature(expression),
        )

    @classmethod
    def from_inventory(cls, item: Any) -> "DiversityVector | None":
        expression = str(getattr(item, "expression", "") or "").strip()
        if not expression:
            return None
        try:
            identity = expression_identity(expression)
            topology = operator_topology(expression)
            structure = structure_signature(expression)
            behavior = behavior_signature(expression)
        except Exception:
            return None
        return cls(
            dataset=str(getattr(item, "dataset", "") or ""),
            fields=tuple(sorted(str(value) for value in getattr(item, "data_fields", ()) if str(value))) or tuple(sorted(extract_fields(expression))),
            field_skeleton=str(getattr(item, "field_skeleton", "") or "") or identity.field_skeleton,
            operator_topology=topology,
            strategy_family=str(getattr(item, "family", "") or "") or topology,
            parent_template=str(getattr(item, "research_direction", "") or ""),
            exact_hash=str(getattr(item, "exact_hash", "") or "") or identity.exact_hash,
            normalized_hash=hashlib.sha256(normalized_expression(expression).encode("utf-8")).hexdigest(),
            structure_signature=str(getattr(item, "structure_signature", "") or "") or structure,
            behavior_signature=str(getattr(item, "behavior_signature", "") or "") or behavior,
        )


@dataclass(frozen=True)
class FeedbackPenalty:
    score: float
    sample_count: int
    failure_counts: tuple[tuple[str, int], ...] = ()
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioSelection:
    accepted: tuple[Any, ...]
    decisions: tuple[dict[str, Any], ...]
    rejection_counts: dict[str, int]
    inventory_hash: str


def select_candidates(
    candidates: Sequence[Any],
    *,
    inventory: Iterable[Any],
    feedback: Any,
    limit: int,
    pending_limit: int = 20,
    limits: PortfolioLimits | None = None,
    mode: str = "shadow",
    eligible_dataset_count: int = 0,
    eligible_field_count: int = 0,
) -> PortfolioSelection:
    """Rank and select candidates against active inventory and feedback.

    ``shadow`` computes the enforce decision but returns the legacy first-N
    order. ``enforce`` returns the diversity-ranked selection and rejection
    counts. Both modes produce identical deterministic decisions for the same
    inputs.
    """

    policy = limits or PortfolioLimits()
    normalized_mode = str(mode or "shadow").strip().lower()
    if normalized_mode not in {"shadow", "enforce"}:
        raise ValueError("portfolio mode must be 'shadow' or 'enforce'")
    candidate_list = list(candidates)
    inventory_list = list(inventory)
    vectors = [DiversityVector.from_candidate(item) for item in candidate_list]
    inventory_vectors = [
        vector
        for item in inventory_list
        if str(getattr(item, "queue_status", "") or "") == _ACTIVE_STATUS
        for vector in [DiversityVector.from_inventory(item)]
        if vector is not None
    ]
    inventory_hash = _inventory_hash(inventory_list)
    active_counts = _counts(inventory_vectors)
    active_hashes = _identity_sets(inventory_vectors)
    scored: list[tuple[tuple[Any, ...], int, DiversityVector, FeedbackPenalty, int]] = []
    for index, (candidate, vector) in enumerate(zip(candidate_list, vectors)):
        penalty = feedback_penalty(vector, feedback, min_samples=policy.feedback_min_samples)
        occupancy_score = _occupancy(vector, active_counts)
        score_key = (
            penalty.score,
            occupancy_score,
            -float(getattr(candidate, "novelty_score", 0.0) or 0.0),
            -float(getattr(candidate, "local_quality_score", 0.0) or 0.0),
            str(getattr(candidate, "expression", "")),
        )
        scored.append((score_key, index, vector, penalty, occupancy_score))
    scored.sort(key=lambda item: item[0])

    selected_indices: list[int] = []
    decisions: list[dict[str, Any]] = []
    selected_counts = {key: dict(value) for key, value in active_counts.items()}
    rejection_counts: dict[str, int] = {}
    for _score_key, index, vector, penalty, occupancy_value in scored:
        reason = _limit_reason(
            vector,
            selected_counts,
            active_hashes,
            policy,
            pending_limit=max(1, int(pending_limit)),
            cycle_limit=max(1, int(limit)),
            eligible_dataset_count=max(0, int(eligible_dataset_count)),
            eligible_field_count=max(0, int(eligible_field_count)),
        )
        would_accept = reason is None and len(selected_indices) < max(0, int(limit))
        if would_accept:
            selected_indices.append(index)
            _increment(selected_counts, vector)
            active_hashes["exact"].add(vector.exact_hash)
            active_hashes["normalized"].add(vector.normalized_hash)
            active_hashes["structure"].add(vector.structure_signature)
            active_hashes["behavior"].add(vector.behavior_signature)
        elif reason is None:
            reason = "PORTFOLIO_LIMIT"
        if reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        decisions.append({
            "expression": str(getattr(candidate_list[index], "expression", "")),
            "decision": "ACCEPT" if would_accept else "REJECT",
            "reason": reason or "SELECTED",
            "would_accept": bool(would_accept),
            "feedback_penalty": {
                "score": penalty.score,
                "sample_count": penalty.sample_count,
                "failure_counts": dict(penalty.failure_counts),
                "refs": list(penalty.refs),
            },
            "occupancy": _occupancy(vector, selected_counts),
            "vector": {
                "dataset": vector.dataset,
                "field_skeleton": vector.field_skeleton,
                "operator_topology": vector.operator_topology,
                "strategy_family": vector.strategy_family,
                "parent_template": vector.parent_template,
            },
        })

    enforce_accepted = tuple(candidate_list[index] for index in selected_indices)
    if normalized_mode == "enforce":
        accepted = enforce_accepted
        actual_rejections = rejection_counts
    else:
        legacy_indices = list(range(min(max(0, int(limit)), len(candidate_list))))
        accepted = tuple(candidate_list[index] for index in legacy_indices)
        shadow_count = sum(1 for item in decisions if not item["would_accept"])
        actual_rejections = (
            {"PORTFOLIO_SHADOW_WOULD_REJECT": shadow_count}
            if shadow_count else {}
        )
    return PortfolioSelection(tuple(accepted), tuple(decisions), actual_rejections, inventory_hash)


def feedback_penalty(vector: DiversityVector, feedback: Any, *, min_samples: int = 2) -> FeedbackPenalty:
    """Return a conservative failure penalty for matching grounded feedback."""

    records = tuple(getattr(feedback, "records", ()) or ())
    matched: list[Any] = []
    for item in records:
        if not bool(getattr(item, "grounded", True)):
            continue
        item_vector = DiversityVector.from_inventory(item)
        if item_vector is None:
            expression = str(getattr(item, "expression", "") or "")
            try:
                item_vector = DiversityVector(
                    dataset=str(getattr(item, "dataset", "") or ""),
                    field_skeleton=str(getattr(item, "field_skeleton", "") or ""),
                    operator_topology=operator_topology(expression),
                    strategy_family=str(getattr(item, "family", "") or ""),
                    parent_template="",
                    exact_hash=exact_hash(expression),
                    normalized_hash="",
                    structure_signature=structure_signature(expression),
                    behavior_signature=behavior_signature(expression),
                )
            except Exception:
                continue
        same_skeleton = bool(vector.field_skeleton and item_vector.field_skeleton == vector.field_skeleton)
        same_topology = bool(vector.operator_topology and item_vector.operator_topology == vector.operator_topology)
        same_dataset = bool(vector.dataset and item_vector.dataset and item_vector.dataset == vector.dataset)
        if same_skeleton or (same_topology and same_dataset):
            matched.append(item)
    if len(matched) < max(1, int(min_samples)):
        return FeedbackPenalty(0.0, len(matched))
    counts: dict[str, int] = {}
    refs: list[str] = []
    for item in matched:
        refs.append(str(getattr(item, "ref_id", "")))
        for failure in tuple(getattr(item, "failure_types", ()) or ()):
            name = str(failure).upper()
            if name in _RISK_WEIGHTS:
                counts[name] = counts.get(name, 0) + 1
    score = min(30.0, sum(_RISK_WEIGHTS[name] * count for name, count in counts.items()))
    return FeedbackPenalty(round(score, 2), len(matched), tuple(sorted(counts.items())), tuple(sorted(set(refs))))


def _limit_reason(
    vector: DiversityVector,
    counts: dict[str, dict[str, int]],
    active_hashes: dict[str, set[str]],
    limits: PortfolioLimits,
    *,
    pending_limit: int,
    cycle_limit: int,
    eligible_dataset_count: int,
    eligible_field_count: int,
) -> str | None:
    if vector.exact_hash in active_hashes["exact"] or vector.normalized_hash in active_hashes["normalized"]:
        return "PORTFOLIO_DUPLICATE"
    if vector.structure_signature in active_hashes["structure"] or vector.behavior_signature in active_hashes["behavior"]:
        return "PORTFOLIO_DUPLICATE"
    if counts["field_skeleton"].get(vector.field_skeleton, 0) >= limits.active_field_skeleton_max:
        return "PORTFOLIO_ACTIVE_FIELD_SKELETON_LIMIT"
    topology_limit = max(1, math.floor(pending_limit * limits.active_topology_fraction))
    if counts["operator_topology"].get(vector.operator_topology, 0) >= topology_limit:
        return "PORTFOLIO_ACTIVE_TOPOLOGY_LIMIT"
    if counts["cycle_field_skeleton"].get(vector.field_skeleton, 0) >= limits.cycle_field_skeleton_max:
        return "PORTFOLIO_CYCLE_FIELD_SKELETON_LIMIT"
    if counts["cycle_topology"].get(vector.operator_topology, 0) >= limits.cycle_topology_max:
        return "PORTFOLIO_CYCLE_TOPOLOGY_LIMIT"
    if counts["cycle_parent"].get(vector.parent_template, 0) >= limits.cycle_parent_max:
        return "PORTFOLIO_CYCLE_PARENT_LIMIT"
    if eligible_dataset_count >= 3:
        dataset_capacity = max(1, math.ceil(cycle_limit / min(eligible_dataset_count, 3)))
        if counts["cycle_dataset"].get(vector.dataset, 0) >= dataset_capacity:
            return "PORTFOLIO_CYCLE_DATASET_COVERAGE_LIMIT"
    if eligible_field_count >= 3:
        field_capacity = max(1, math.ceil(cycle_limit / min(eligible_field_count, 3)))
        if any(counts["cycle_field"].get(field, 0) >= field_capacity for field in vector.fields):
            return "PORTFOLIO_CYCLE_FIELD_COVERAGE_LIMIT"
    return None


def _counts(vectors: Iterable[DiversityVector]) -> dict[str, dict[str, int]]:
    result = {
        "dataset": {}, "field": {}, "field_skeleton": {}, "operator_topology": {}, "strategy_family": {},
        "cycle_field_skeleton": {}, "cycle_topology": {}, "cycle_parent": {},
        "cycle_dataset": {}, "cycle_field": {},
    }
    for vector in vectors:
        _increment(result, vector, active=True)
    return result


def _increment(counts: dict[str, dict[str, int]], vector: DiversityVector, *, active: bool = False) -> None:
    keys = ("dataset", "field_skeleton", "operator_topology", "strategy_family")
    if not active:
        keys += ("cycle_field_skeleton", "cycle_topology", "cycle_parent", "cycle_dataset")
    for key in keys:
        value = {
            "dataset": vector.dataset,
            "field_skeleton": vector.field_skeleton,
            "operator_topology": vector.operator_topology,
            "strategy_family": vector.strategy_family,
            "cycle_field_skeleton": vector.field_skeleton,
            "cycle_topology": vector.operator_topology,
            "cycle_parent": vector.parent_template,
            "cycle_dataset": vector.dataset,
        }[key]
        counts[key][value] = counts[key].get(value, 0) + 1
    for field in vector.fields:
        counts["field"][field] = counts["field"].get(field, 0) + 1
        if not active:
            counts["cycle_field"][field] = counts["cycle_field"].get(field, 0) + 1


def _identity_sets(vectors: Iterable[DiversityVector]) -> dict[str, set[str]]:
    return {
        "exact": {item.exact_hash for item in vectors if item.exact_hash},
        "normalized": {item.normalized_hash for item in vectors if item.normalized_hash},
        "structure": {item.structure_signature for item in vectors if item.structure_signature},
        "behavior": {item.behavior_signature for item in vectors if item.behavior_signature},
    }


def _occupancy(vector: DiversityVector, counts: dict[str, dict[str, int]]) -> int:
    return sum((
        counts["dataset"].get(vector.dataset, 0),
        sum(counts["field"].get(field, 0) for field in vector.fields),
        counts["field_skeleton"].get(vector.field_skeleton, 0),
        counts["operator_topology"].get(vector.operator_topology, 0),
        counts["strategy_family"].get(vector.strategy_family, 0),
    ))


def _inventory_hash(items: Iterable[Any]) -> str:
    payload = []
    for item in items:
        payload.append({
            "request_hash": str(getattr(item, "request_hash", "") or ""),
            "candidate_id": str(getattr(item, "candidate_id", "") or ""),
            "queue_status": str(getattr(item, "queue_status", "") or ""),
            "expression": str(getattr(item, "expression", "") or ""),
            "field_skeleton": str(getattr(item, "field_skeleton", "") or ""),
            "family": str(getattr(item, "family", "") or ""),
        })
    encoded = json.dumps(sorted(payload, key=lambda item: (item["request_hash"], item["candidate_id"])), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
