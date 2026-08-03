"""Authoritative candidate generation service.

This is the single source of candidates for the production pipeline.
It does NOT call the platform, write CSVs, execute simulations, or submit alphas.

Production usage:
    svc = CandidateGenerationService(database)
    batch = svc.generate(limit=batch_size)
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha_mining.domain.expression_normalization import expression_identity
from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
from alpha_mining.generation.validation import ExpressionCatalog


@dataclass(frozen=True)
class CandidateProposal:
    candidate_id: str
    topic_id: str
    hypothesis_id: str
    research_family: str
    strategy_family: str
    mutation_type: str
    mechanism: str
    dataset: str
    expression: str
    parent_template: str
    generator_source: str
    exact_hash: str
    parameter_skeleton: str
    field_skeleton: str
    knowledge_refs: tuple[str, ...] = ()
    economic_rationale: str = ""
    expected_signal: str = ""
    expected_turnover_behavior: str = ""
    repair_origin: str = ""


@dataclass(frozen=True)
class CandidateGenerationBatch:
    candidates: tuple[CandidateProposal, ...]
    selected_topic_ids: tuple[str, ...]
    selected_families: tuple[str, ...]
    rejected_by_reason: dict[str, int]
    generation_state: str
    deferred_reason: str


# Strategy families (correspond to ConsultantGenerator's internal classification)
_STRATEGY_FAMILIES = ("momentum", "reversal", "volatility", "fundamental", "balanced")


def _classify_strategy_family(mechanism: str, family: str) -> str:
    keyword_groups = {
        "momentum": ("momentum", "trend", "growth"),
        "reversal": ("reversal", "mean reversion", "contrarian"),
        "volatility": ("volatility", "risk"),
        "fundamental": ("fundamental", "value", "quality", "profitability"),
    }
    for text in (mechanism, family):
        normalized = " ".join(str(text or "").lower().replace("_", " ").split())
        for category, keywords in keyword_groups.items():
            if any(kw in normalized for kw in keywords):
                return category
    return "balanced"


class CandidateGenerationService:
    """Single authoritative entry point for candidate generation.

    The service reads research specs from the database, applies canonical
    screening, and returns a CandidateGenerationBatch. It does not interact
    with the platform, does not write CSVs, and does not execute simulations.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        generator: Any | None = None,
        idea_generator: Any | None = None,
        feedback: Any | None = None,
        policy: CandidateScreeningPolicy | None = None,
        catalog: ExpressionCatalog | None = None,
        region: str | None = None,
        universe: str | None = None,
        delay: int | str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.database = Path(database)
        self._policy = policy or CandidateScreeningPolicy(
            catalog=catalog,
            region=region,
            universe=universe,
            delay=delay,
        )
        self._rng = rng or random.Random()

        # Lazy imports to allow offline usage without full platform deps
        if generator is None:
            from alpha_mining.generator.consultant_generator import ConsultantGenerator
            generator = ConsultantGenerator()
        self._generator = generator
        self._idea_generator = idea_generator
        self._feedback = feedback

    def generate(self, *, limit: int) -> CandidateGenerationBatch:
        """Generate up to *limit* screened candidate proposals."""
        specs = self._load_research_specs()
        if not specs:
            return CandidateGenerationBatch(
                candidates=(),
                selected_topic_ids=(),
                selected_families=(),
                rejected_by_reason={},
                generation_state="NO_RESEARCH_SPECS",
                deferred_reason="no active research specifications are available",
            )

        # Arm weights from feedback if available
        arm_weights = self._arm_weights()

        # Round-level deduplication state
        round_seen_hashes: set[str] = set()
        round_seen_skeletons: set[str] = set()
        rejected_by_reason: dict[str, int] = {}

        # Group specs by strategy family for round-robin
        family_buckets: dict[str, list[Any]] = {}
        for spec in specs:
            sf = _classify_strategy_family(spec["mechanism"], spec["family"])
            family_buckets.setdefault(sf, []).append(spec)

        families_in_order = sorted(
            family_buckets.keys(),
            key=lambda f: -(arm_weights.get(f, 1.0)),
        )

        # Shuffle within each family bucket
        for bucket in family_buckets.values():
            self._rng.shuffle(bucket)

        # Round-robin across families
        accepted: list[CandidateProposal] = []
        selected_topic_ids: set[str] = set()
        selected_families: set[str] = set()
        accepted_per_family: dict[str, int] = {}
        family_index = 0
        attempts = 0
        max_attempts = limit * 20 + len(specs) * 14

        while len(accepted) < limit and attempts < max_attempts:
            attempts += 1
            if not families_in_order:
                break
            sf = families_in_order[family_index % len(families_in_order)]
            family_index += 1
            if arm_weights.get(sf, 1.0) <= 0:
                continue
            if arm_weights.get(sf, 1.0) < 1.0 and accepted_per_family.get(sf, 0) >= 1:
                continue
            bucket = family_buckets.get(sf, [])
            if not bucket:
                continue

            spec = bucket[attempts % len(bucket)]
            candidates = self._generator.generate(
                hypothesis_id=spec["hypothesis_id"],
                family=spec["family"],
                mechanism=spec["mechanism"],
                horizon=spec["horizon"],
                fields=spec["fields"],
                dataset=spec["dataset"],
            )
            if not candidates:
                continue

            self._rng.shuffle(candidates)
            for candidate in candidates:
                if len(accepted) >= limit:
                    break
                if not tuple(getattr(candidate, "knowledge_refs", ()) or ()):
                    rejected_by_reason[RejectionReason.KNOWLEDGE_MISSING.value] = (
                        rejected_by_reason.get(RejectionReason.KNOWLEDGE_MISSING.value, 0) + 1
                    )
                    continue
                reason = self._policy.screen_expression(
                    candidate.expression,
                    round_seen_hashes=round_seen_hashes,
                    round_seen_skeletons=round_seen_skeletons,
                    expected_dataset_id=spec["dataset"],
                )
                if reason is not None and reason != RejectionReason.NONE:
                    rejected_by_reason[reason.value] = rejected_by_reason.get(reason.value, 0) + 1
                    continue

                try:
                    identity = expression_identity(candidate.expression)
                except Exception:
                    rejected_by_reason["INVALID_IDENTITY"] = (
                        rejected_by_reason.get("INVALID_IDENTITY", 0) + 1
                    )
                    continue

                round_seen_hashes.add(identity.exact_hash)
                round_seen_skeletons.add(identity.field_skeleton)
                selected_topic_ids.add(spec["topic_id"])
                strategy_family = _classify_strategy_family(spec["mechanism"], spec["family"])
                selected_families.add(strategy_family)
                accepted_per_family[strategy_family] = accepted_per_family.get(strategy_family, 0) + 1

                proposal = CandidateProposal(
                    candidate_id=candidate.candidate_id,
                    topic_id=spec["topic_id"],
                    hypothesis_id=spec["hypothesis_id"],
                    research_family=spec["family"],
                    strategy_family=strategy_family,
                    mutation_type=candidate.mutation_type,
                    mechanism=spec["mechanism"],
                    dataset=spec["dataset"],
                    expression=candidate.expression,
                    parent_template=candidate.mutation_type,
                    generator_source="ConsultantGenerator",
                    exact_hash=identity.exact_hash,
                    parameter_skeleton=identity.parameter_skeleton,
                    field_skeleton=identity.field_skeleton,
                    knowledge_refs=tuple(getattr(candidate, "knowledge_refs", ()) or ()),
                    economic_rationale=str(getattr(candidate, "economic_rationale", "") or ""),
                    expected_signal=str(getattr(candidate, "expected_signal", "") or ""),
                    expected_turnover_behavior=str(
                        getattr(candidate, "expected_turnover_behavior", "") or ""
                    ),
                    repair_origin=str(getattr(candidate, "repair_origin", "") or ""),
                )
                accepted.append(proposal)

        return CandidateGenerationBatch(
            candidates=tuple(accepted),
            selected_topic_ids=tuple(sorted(selected_topic_ids)),
            selected_families=tuple(sorted(selected_families)),
            rejected_by_reason=rejected_by_reason,
            generation_state="READY" if accepted else "NO_CANDIDATES",
            deferred_reason="" if accepted else "no candidates passed screening",
        )

    def _load_research_specs(self) -> list[dict]:
        try:
            with sqlite3.connect(self.database) as con:
                rows = con.execute(
                    """SELECT h.hypothesis_id, COALESCE(t.topic_id,''), COALESCE(t.category,'UNCLASSIFIED'),
                              COALESCE(h.mechanism,h.statement_en,h.statement_cn,''),
                              COALESCE(h.horizon,'medium'), m.data_field, COALESCE(m.dataset_id,'UNKNOWN')
                       FROM hypotheses h
                       JOIN research_topics t ON t.topic_id=h.topic_id
                       JOIN data_mappings m ON m.hypothesis_id=h.hypothesis_id
                       WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1
                       ORDER BY h.created_at, h.hypothesis_id, m.field_quality_score DESC"""
                ).fetchall()
        except Exception:
            return []

        grouped: dict[str, dict] = {}
        for row in rows:
            hid = str(row[0])
            if hid not in grouped:
                grouped[hid] = {
                    "hypothesis_id": hid,
                    "topic_id": str(row[1]),
                    "family": str(row[2]),
                    "mechanism": str(row[3]),
                    "horizon": str(row[4]),
                    "fields": (str(row[5]),),
                    "dataset": str(row[6]),
                }
            else:
                existing = grouped[hid]["fields"]
                field = str(row[5])
                if field not in existing:
                    grouped[hid]["fields"] = (*existing, field)
        return list(grouped.values())

    def _arm_weights(self) -> dict[str, float]:
        """Return per-strategy-family weights from arm metrics if available."""
        try:
            from alpha_mining.scheduler.arm_metrics import ResearchArmTracker, ArmDimensions
            with sqlite3.connect(self.database) as con:
                rows = con.execute(
                    "SELECT family, sampling_weight FROM research_arm_metrics"
                ).fetchall()
            weights: dict[str, float] = {}
            for family, w in rows:
                sf = _classify_strategy_family("", str(family))
                weights[sf] = min(weights.get(sf, float(w)), float(w))
            return weights
        except Exception:
            return {}
