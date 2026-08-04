"""Knowledge-grounded LLM refinement and deterministic local quality gates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    exact_hash,
    extract_fields,
    extract_functions,
    normalized_expression,
    operator_topology,
    structure_signature,
)
from alpha_mining.domain.operator_registry import GROUPS
from alpha_mining.generation.snapshots import FeedbackSummary, LocalSnapshots
from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.knowledge.worldquant_repository import KnowledgeIntent, KnowledgeContext, WorldQuantKnowledgeRepository


class StructuredLLM(Protocol):
    model_id: str

    def generate_json(self, *, system_prompt: str, user_prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]: ...


class LLMUnavailable(RuntimeError):
    """A model transport or structured-response failure, without secret detail."""


@dataclass(frozen=True)
class AcceptedCandidate:
    expression: str
    settings: dict[str, Any]
    datasets: tuple[str, ...]
    parent_seed: str
    research_direction: str
    hypothesis: str
    economic_rationale: str
    knowledge_refs: tuple[str, ...]
    feedback_refs: tuple[str, ...]
    anti_corr_design: str
    expected_turnover_behavior: str
    local_quality_score: float
    novelty_score: float
    self_corr_risk_score: float
    quality_evidence: dict[str, Any]
    generator_source: str


@dataclass(frozen=True)
class HighQualityResult:
    seeds: tuple[Any, ...]
    knowledge: KnowledgeContext
    accepted: tuple[AcceptedCandidate, ...]
    rejections: dict[str, int]
    llm_candidates: int


_GHOST_OPERATORS = frozenset({"exp", "ts_skewness", "ts_ir", "vector_neut", "regression_neut", "if_else", "bucket"})
_FAILURE_NAMES = ("SELF_CORRELATION", "PROD_CORRELATION", "LOW_SHARPE", "LOW_FITNESS", "HIGH_TURNOVER", "CONCENTRATED_WEIGHT")


class HighQualityGenerator:
    def __init__(
        self,
        *,
        llm: StructuredLLM,
        kernel: Any,
        knowledge_repository: WorldQuantKnowledgeRepository | None = None,
        correlation_ceiling: float = 0.65,
        history_ceiling: float = 0.72,
        quality_threshold: float = 75.0,
    ) -> None:
        self.llm = llm
        self.kernel = kernel
        self.knowledge_repository = knowledge_repository or WorldQuantKnowledgeRepository()
        self.correlation_ceiling = float(correlation_ceiling)
        self.history_ceiling = float(history_ceiling)
        self.quality_threshold = float(quality_threshold)

    def generate(self, snapshots: LocalSnapshots, *, cycle_id: str, candidates_per_cycle: int) -> HighQualityResult:
        raw_seeds = list(self.kernel.generate(snapshots))
        seeds, seed_rejections = self._select_seeds(raw_seeds, snapshots.feedback)
        if not seeds:
            return HighQualityResult((), _empty_context(), (), seed_rejections, 0)
        fields = tuple(sorted({field for seed in seeds for field in extract_fields(str(getattr(seed, "expression", ""))) if field in snapshots.catalog.fields}))
        datasets = {snapshots.catalog.fields[field].dataset_id for field in fields}
        dataset = next(iter(datasets)) if len(datasets) == 1 else ""
        failure_category = " ".join(sorted(snapshots.feedback.failure_counts))
        knowledge = self.knowledge_repository.retrieve(
            dataset=dataset,
            fields=fields,
            mechanism="fundamental hypothesis operator diversity",
            failure_category=failure_category,
            intent=KnowledgeIntent.IDEA_GENERATION,
        )
        if not knowledge.snippets:
            seed_rejections["KNOWLEDGE_UNAVAILABLE"] = seed_rejections.get("KNOWLEDGE_UNAVAILABLE", 0) + 1
            return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, 0)
        plan = self._call_llm(
            system_prompt="You are a constrained WorldQuant alpha researcher. Return JSON only and never invent catalog items or references.",
            user_prompt=self._research_prompt(snapshots, seeds, knowledge, cycle_id),
            json_schema=_plan_schema(),
        )
        allowed_refs = {item.ref_id for item in knowledge.snippets}
        plan_refs = _string_set(plan.get("knowledge_refs"))
        if not plan_refs or not plan_refs <= allowed_refs:
            seed_rejections["HALLUCINATED_KNOWLEDGE_REF"] = seed_rejections.get("HALLUCINATED_KNOWLEDGE_REF", 0) + 1
            return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, 0)
        proposed = self._call_llm(
            system_prompt="Generate a few valid FASTEXPR candidates from the plan. Return JSON only.",
            user_prompt=self._candidate_prompt(snapshots, seeds, knowledge, plan),
            json_schema=_candidate_schema(),
        )
        candidate_rows = proposed.get("candidates") if isinstance(proposed, dict) else []
        if not isinstance(candidate_rows, list):
            candidate_rows = []
        critique = self._call_llm(
            system_prompt="Critically audit these proposed alpha expressions. Reject hallucinations, clones, unjustified mechanisms, and correlation risk. Return JSON only.",
            user_prompt=json.dumps({"plan": plan, "candidates": candidate_rows}, ensure_ascii=False),
            json_schema=_critique_schema(),
        )
        approvals = critique.get("approved") if isinstance(critique, dict) else []
        if not isinstance(approvals, list):
            approvals = []
        accepted: list[AcceptedCandidate] = []
        used_behaviors: set[str] = set()
        used_pairs: set[tuple[str, tuple[str, ...]]] = set()
        for index, row in enumerate(candidate_rows):
            if len(accepted) >= min(5, max(1, int(candidates_per_cycle))):
                break
            if not isinstance(row, dict):
                _reject(seed_rejections, "INVALID_LLM_CANDIDATE")
                continue
            approval = approvals[index] if index < len(approvals) and isinstance(approvals[index], dict) else {}
            if not approval.get("approved"):
                _reject(seed_rejections, "LLM_CRITIQUE_REJECTED")
                continue
            outcome = self._validate_candidate(
                row, plan, snapshots, seeds, knowledge, used_behaviors, used_pairs,
            )
            if isinstance(outcome, str):
                _reject(seed_rejections, outcome)
                continue
            accepted.append(outcome)
            used_behaviors.add(behavior_signature(outcome.expression))
            used_pairs.add((operator_topology(outcome.expression), tuple(sorted(extract_fields(outcome.expression)))))
        return HighQualityResult(tuple(seeds), knowledge, tuple(accepted), seed_rejections, len(candidate_rows))

    def _call_llm(self, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.llm.generate_json(**kwargs)
        except Exception as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        if not isinstance(response, dict):
            raise LLMUnavailable("invalid structured response")
        return response

    def _select_seeds(self, candidates: list[Any], feedback: FeedbackSummary) -> tuple[list[Any], dict[str, int]]:
        rejections: dict[str, int] = {}
        known_exact = {exact_hash(item.expression) for item in feedback.records if item.expression}
        known_structures = {structure_signature(item.expression) for item in feedback.records if item.expression}
        selected: list[Any] = []
        behavior_seen: set[str] = set()
        pair_seen: set[tuple[str, tuple[str, ...]]] = set()
        for candidate in sorted(candidates, key=lambda item: float(getattr(item, "score", 0.0)), reverse=True):
            expression = str(getattr(candidate, "expression", "") or "").strip()
            fields = tuple(sorted(extract_fields(expression)))
            pair = (operator_topology(expression), fields)
            if not expression or exact_hash(expression) in known_exact:
                _reject(rejections, "HISTORY_EXACT_DUPLICATE")
                continue
            if structure_signature(expression) in known_structures:
                _reject(rejections, "HISTORY_STRUCTURE_DUPLICATE")
                continue
            if behavior_signature(expression) in behavior_seen or pair in pair_seen:
                _reject(rejections, "SEED_DIVERSITY_DUPLICATE")
                continue
            selected.append(candidate)
            behavior_seen.add(behavior_signature(expression))
            pair_seen.add(pair)
            if len(selected) >= 3:
                break
        return selected, rejections

    def _validate_candidate(
        self,
        row: dict[str, Any],
        plan: dict[str, Any],
        snapshots: LocalSnapshots,
        seeds: list[Any],
        knowledge: KnowledgeContext,
        used_behaviors: set[str],
        used_pairs: set[tuple[str, tuple[str, ...]]],
    ) -> AcceptedCandidate | str:
        expression = str(row.get("expression") or "").strip()
        if not expression:
            return "EMPTY_EXPRESSION"
        functions = set(extract_functions(expression))
        if functions & _GHOST_OPERATORS:
            return "GHOST_OPERATOR"
        if not functions <= set(snapshots.catalog.operators):
            return "UNKNOWN_OPERATOR"
        fields = tuple(sorted(extract_fields(expression)))
        if not fields or not set(fields) <= set(snapshots.catalog.fields):
            return "UNKNOWN_FIELD"
        datasets = {snapshots.catalog.fields[field].dataset_id for field in fields}
        if len(datasets) != 1:
            return "CROSS_DATASET"
        allowed_fields = _string_set(plan.get("fields_to_use"))
        allowed_operators = _string_set(plan.get("operators_to_use"))
        if not set(fields) <= allowed_fields or not functions <= allowed_operators:
            return "PLAN_SCOPE_VIOLATION"
        allowed_refs = {item.ref_id for item in knowledge.snippets}
        refs = _string_set(row.get("knowledge_refs"))
        if not refs or not refs <= allowed_refs:
            return "HALLUCINATED_KNOWLEDGE_REF"
        feedback_refs = _string_set(row.get("feedback_patterns_used"))
        known_feedback_refs = {item.ref_id for item in snapshots.feedback.records}
        if not feedback_refs <= known_feedback_refs:
            return "HALLUCINATED_FEEDBACK_REF"
        parents = {str(getattr(seed, "expression", "")) for seed in seeds}
        parent = str(row.get("parent_seed") or "")
        if parent not in parents:
            return "UNKNOWN_PARENT_SEED"
        validator = LocalExpressionValidator(snapshots.catalog, allow_stale_catalog=True)
        issues = [issue for issue in validator.validate(expression, expected_dataset_id=next(iter(datasets))) if not (issue.code == "UNKNOWN_FIELD" and issue.message in GROUPS)]
        if issues:
            return "LOCAL_VALIDATION_" + issues[0].code
        history = [item.expression for item in snapshots.feedback.records if item.expression]
        if exact_hash(expression) in {exact_hash(item) for item in history}:
            return "EXACT_DUPLICATE"
        normalized_hash = _hash(normalized_expression(expression))
        if normalized_hash in {_hash(normalized_expression(item)) for item in history}:
            return "NORMALIZED_DUPLICATE"
        struct = structure_signature(expression)
        if struct in {structure_signature(item) for item in history}:
            return "STRUCTURE_DUPLICATE"
        self_risk = max((_similarity(expression, item.expression) for item in snapshots.feedback.self_corr_risk if item.expression), default=0.0)
        if self_risk >= self.correlation_ceiling:
            return "SELF_CORRELATION_RISK"
        history_risk = max((_similarity(expression, item) for item in history), default=0.0)
        if history_risk >= self.history_ceiling:
            return "HISTORY_SIMILARITY"
        behavior = behavior_signature(expression)
        pair = (operator_topology(expression), fields)
        if behavior in used_behaviors:
            return "BEHAVIOR_DUPLICATE"
        if pair in used_pairs:
            return "OPERATOR_FIELD_DUPLICATE"
        if _has_short_window(expression):
            return "SHORT_WINDOW"
        if _bare_price_expression(fields):
            return "BARE_PRICE_RISK"
        rationale = str(row.get("economic_rationale") or "").strip()
        anti = str(row.get("anti_corr_design") or "").strip()
        if len(rationale) < 20 or len(anti) < 12:
            return "WEAK_ECONOMIC_MECHANISM"
        score, evidence = self._quality_score(
            expression, fields, refs, feedback_refs, rationale, anti, snapshots.feedback, self_risk,
        )
        if score < self.quality_threshold:
            return "LOW_LOCAL_QUALITY"
        settings = _settings(row.get("settings"), snapshots.catalog.info)
        return AcceptedCandidate(
            expression, settings, tuple(sorted(datasets)), parent, str(plan.get("research_direction") or ""), str(plan.get("hypothesis") or ""),
            rationale, tuple(sorted(refs)), tuple(sorted(feedback_refs)), anti,
            str(plan.get("expected_turnover_behavior") or ""), score,
            max(0.0, 1.0 - history_risk), self_risk, evidence, "LLM_REFINED_V50",
        )

    def _quality_score(self, expression: str, fields: tuple[str, ...], refs: set[str], feedback_refs: set[str], rationale: str, anti: str, feedback: FeedbackSummary, self_risk: float) -> tuple[float, dict[str, Any]]:
        value = 30.0
        value += 16.0 if refs else 0.0
        value += 15.0 if len(rationale) >= 40 else 8.0
        value += 12.0 if len(anti) >= 20 else 6.0
        value += min(12.0, 4.0 + len(fields) * 4.0)
        value += 7.0 if not feedback_refs or feedback_refs <= {item.ref_id for item in feedback.records} else 0.0
        value += max(0.0, 8.0 - self_risk * 12.0)
        evidence = {
            "local_quality_score_definition": "local candidate ranking only; not platform Sharpe or Fitness",
            "catalog_legal": True,
            "knowledge_grounded": bool(refs),
            "economic_mechanism_length": len(rationale),
            "anti_corr_design_length": len(anti),
            "field_count": len(fields),
            "feedback_refs": sorted(feedback_refs),
            "self_corr_risk_score": round(self_risk, 4),
        }
        return round(min(100.0, value), 2), evidence

    @staticmethod
    def _research_prompt(snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext, cycle_id: str) -> str:
        catalog = snapshots.catalog
        payload = {
            "cycle_id": cycle_id,
            "catalog": {
                "datasets": sorted(catalog.datasets),
                "fields": [{"id": item.field_id, "dataset": item.dataset_id, "description": item.description[:160]} for item in list(catalog.fields.values())[:80]],
                "operators": sorted(catalog.operators),
            },
            "feedback": {
                "positive_refs": [item.ref_id for item in snapshots.feedback.positive[:12]],
                "near_pass_refs": [item.ref_id for item in snapshots.feedback.near_pass[:12]],
                "failure_counts": snapshots.feedback.failure_counts,
                "self_corr_refs": [item.ref_id for item in snapshots.feedback.self_corr_risk[:12]],
            },
            "knowledge": [{"ref_id": item.ref_id, "text": item.text} for item in knowledge.snippets],
            "v50_seeds": [str(getattr(item, "expression", "")) for item in seeds],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _candidate_prompt(snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext, plan: dict[str, Any]) -> str:
        payload = {
            "plan": plan,
            "allowed_fields": sorted(snapshots.catalog.fields),
            "allowed_operators": sorted(snapshots.catalog.operators),
            "allowed_knowledge_refs": [item.ref_id for item in knowledge.snippets],
            "allowed_feedback_refs": [item.ref_id for item in snapshots.feedback.records],
            "allowed_parent_seeds": [str(getattr(item, "expression", "")) for item in seeds],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _empty_context() -> KnowledgeContext:
    return KnowledgeContext((), "NO_SEEDS")


def _string_set(value: object) -> set[str]:
    return {str(item).strip() for item in value} if isinstance(value, (list, tuple, set)) else set()


def _settings(value: object, catalog_info: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "alpha_type": str(source.get("alpha_type") or "REGULAR"),
        "region": str(source.get("region") or catalog_info.get("region") or "USA"),
        "universe": str(source.get("universe") or catalog_info.get("universe") or "TOP3000"),
        "delay": int(source.get("delay") or catalog_info.get("delay") or 1),
        "decay": int(source.get("decay") or 4),
        "neutralization": str(source.get("neutralization") or "MARKET"),
        "truncation": float(source.get("truncation") or 0.08),
        "language": str(source.get("language") or "FASTEXPR"),
    }


def _similarity(left: str, right: str) -> float:
    a = set(re.findall(r"[a-z_]+|\d+", behavior_signature(left).lower()))
    b = set(re.findall(r"[a-z_]+|\d+", behavior_signature(right).lower()))
    return len(a & b) / len(a | b) if a and b else 0.0


def _has_short_window(expression: str) -> bool:
    for fn, window in re.findall(r"\b(ts_[a-z_]+)\([^)]*?,\s*(\d+)\s*\)", expression.lower()):
        if fn != "ts_corr" and int(window) < 21:
            return True
        if fn == "ts_corr" and int(window) < 42:
            return True
    return False


def _bare_price_expression(fields: tuple[str, ...]) -> bool:
    return bool(fields) and all(field.lower() in {"close", "open", "high", "low", "vwap", "price"} for field in fields)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject(target: dict[str, int], reason: str) -> None:
    target[reason] = target.get(reason, 0) + 1


def _plan_schema() -> dict[str, Any]:
    return {"type": "object", "required": ["research_direction", "hypothesis", "economic_mechanism", "expected_horizon", "fields_to_use", "operators_to_use", "anti_correlation_plan", "expected_turnover_behavior", "historical_failures_to_avoid", "knowledge_refs"]}


def _candidate_schema() -> dict[str, Any]:
    return {"type": "object", "required": ["candidates"]}


def _critique_schema() -> dict[str, Any]:
    return {"type": "object", "required": ["approved"]}
