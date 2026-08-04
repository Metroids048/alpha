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
from alpha_mining.domain.operator_registry import BASE_VARS, GROUPS
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
_PLAN_OPERATOR_ALIASES = {
    "multiply": "mul", "times": "mul", "divide": "div", "division": "div",
    "subtract": "sub", "minus": "sub", "plus": "add",
}
_REPAIRABLE_DRAFT_REJECTIONS = frozenset({
    "EMPTY_EXPRESSION", "INVALID_LLM_CANDIDATE", "CROSS_DATASET", "PLAN_SCOPE_VIOLATION",
    "LOCAL_VALIDATION_UNKNOWN_FIELD",
    "LOCAL_VALIDATION_UNKNOWN_OPERATOR", "LOCAL_VALIDATION_INVALID_ARITY", "LOCAL_VALIDATION_FASTPLUS",
    "LOCAL_VALIDATION_INVALID_SYNTAX", "SHORT_WINDOW", "BARE_PRICE_RISK", "WEAK_ECONOMIC_MECHANISM",
})


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
        plan = self._canonicalize_plan_operators(plan, snapshots.catalog.operators)
        allowed_refs = {item.ref_id for item in knowledge.snippets}
        allowed_plan_fields = self._research_field_ids(snapshots, seeds)
        plan_issues = self._plan_issues(plan, snapshots, allowed_plan_fields, allowed_refs)
        if plan_issues:
            for issue in plan_issues:
                _reject(seed_rejections, issue)
            plan = self._call_llm(
                system_prompt=(
                    "Repair a research plan so it is exactly grounded in the supplied local catalog and references. "
                    "Return JSON only. Do not preserve an invalid field, operator, reference, or cross-dataset mix."
                ),
                user_prompt=json.dumps({
                    "invalid_plan": plan,
                    "deterministic_rejections": plan_issues,
                    "exact_plan_scope": {
                        "fields": sorted(allowed_plan_fields),
                        "field_datasets": {
                            field: snapshots.catalog.fields[field].dataset_id for field in sorted(allowed_plan_fields)
                        },
                        "operators": sorted(set(snapshots.catalog.operators) - _GHOST_OPERATORS),
                        "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
                        "knowledge_refs": sorted(allowed_refs),
                    },
                    "requirements": [
                        "select fields from exactly one dataset",
                        "use only exact field, operator and knowledge reference IDs from exact_plan_scope",
                        "treat parent seeds as structural inspiration only; do not copy their unavailable identifiers",
                        "avoid windows below 21, and use at least 42 for ts_corr",
                    ],
                }, ensure_ascii=False, sort_keys=True),
                json_schema=_plan_schema(),
            )
            plan = self._canonicalize_plan_operators(plan, snapshots.catalog.operators)
            repaired_plan_issues = self._plan_issues(plan, snapshots, allowed_plan_fields, allowed_refs)
            if repaired_plan_issues:
                for issue in repaired_plan_issues:
                    _reject(seed_rejections, issue)
                return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, 0)
        proposed = self._call_llm(
            system_prompt=(
                "Generate a few valid FASTEXPR candidates from the plan. Return JSON only. "
                "Do not create field/window/constant clones of a seed. Do not use a generic "
                "rank(ts_mean(volume,...)/adv...) leg as a cosmetic multiplier. Each candidate "
                "must use a materially distinct mechanism and be strong enough for a later strict audit."
            ),
            user_prompt=self._candidate_prompt(snapshots, seeds, knowledge, plan),
            json_schema=_candidate_schema(),
        )
        candidate_rows = proposed.get("candidates") if isinstance(proposed, dict) else []
        if not isinstance(candidate_rows, list):
            candidate_rows = []
        critique = self._call_llm(
            system_prompt=(
                "Critically audit these proposed alpha expressions. Reject only concrete violations: "
                "invented catalog items or refs, a parent clone, absent economic mechanism, explicit "
                "history-correlation risk, prohibited short window, or unjustified operator stacking. "
                "Do not reject based on unsupported speculation about coverage, point-in-time availability, "
                "or platform behavior when the supplied catalog and evidence do not establish it. Return JSON only."
            ),
            user_prompt=json.dumps({"plan": plan, "candidates": candidate_rows}, ensure_ascii=False),
            json_schema=_critique_schema(),
        )
        approvals = critique.get("approved") if isinstance(critique, dict) else []
        if isinstance(approvals, bool):
            approvals = [{"approved": approvals} for _ in candidate_rows]
        elif not isinstance(approvals, list):
            approvals = []
        accepted, used_behaviors, used_pairs = self._screen_rows(
            candidate_rows, approvals, plan, snapshots, seeds, knowledge, seed_rejections,
            candidates_per_cycle,
        )
        all_critic_rejected = seed_rejections.get("LLM_CRITIQUE_REJECTED", 0) == len(candidate_rows)
        deterministic_draft_rejected = any(
            seed_rejections.get(reason, 0) for reason in _REPAIRABLE_DRAFT_REJECTIONS
        )
        repaired_count = 0
        if not accepted and candidate_rows and (all_critic_rejected or deterministic_draft_rejected):
            repaired = self._call_llm(
                system_prompt=(
                    "Repair rejected alpha candidates as a constrained quantitative researcher. Return JSON only. "
                    "This is one correction pass, not permission to weaken a rule: every repaired expression "
                    "will be revalidated against the exact allowed fields, operators, knowledge references, "
                    "feedback references, parent seeds, local syntax, duplicate, and correlation gates."
                ),
                user_prompt=json.dumps({
                    "plan": plan,
                    "rejected_candidates": candidate_rows,
                    "critique": critique,
                    "exact_expression_scope": {
                        "fields": sorted(_string_set(plan.get("fields_to_use"))),
                        "operators": sorted(_string_set(plan.get("operators_to_use"))),
                        "group_labels": sorted(GROUPS),
                        "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
                        "knowledge_refs": sorted(allowed_refs),
                        "feedback_refs": sorted(item.ref_id for item in snapshots.feedback.records),
                        "parent_seeds": [str(getattr(item, "expression", "")) for item in seeds],
                    },
                    "requirements": [
                        "produce materially different economic mechanisms, not field/window/constant clones",
                        "use only the exact items in exact_expression_scope; group_labels are grouping arguments, not fields",
                        "remove every forbidden identifier even when it appeared in a parent seed",
                        "never invent a field, operator, reference, group label, setting, or platform rule",
                        "include every required candidate field and explain the mechanism",
                    ],
                }, ensure_ascii=False),
                json_schema=_candidate_schema(),
            )
            repaired_rows = repaired.get("candidates") if isinstance(repaired.get("candidates"), list) else []
            repaired_count = len(repaired_rows)
            if repaired_rows:
                repaired_critique = self._call_llm(
                    system_prompt=(
                        "Audit repaired alpha candidates against only the supplied plan and exact scope. "
                        "Reject concrete hallucinations, scope violations, parent clones, absent mechanisms, "
                        "prohibited short windows, or explicit supplied correlation risks. Do not speculate "
                        "about coverage, lookahead, or platform behavior. Return one approval object per candidate."
                    ),
                    user_prompt=json.dumps({
                        "plan": plan,
                        "exact_expression_scope": {
                            "fields": sorted(_string_set(plan.get("fields_to_use"))),
                            "operators": sorted(_string_set(plan.get("operators_to_use"))),
                            "group_labels": sorted(GROUPS),
                            "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
                            "knowledge_refs": sorted(allowed_refs),
                            "feedback_refs": sorted(item.ref_id for item in snapshots.feedback.records),
                            "parent_seeds": [str(getattr(item, "expression", "")) for item in seeds],
                        },
                        "candidates": repaired_rows,
                    }, ensure_ascii=False),
                    json_schema=_critique_schema(),
                )
                repaired_approvals = repaired_critique.get("approved") if isinstance(repaired_critique, dict) else []
                if isinstance(repaired_approvals, bool):
                    repaired_approvals = [{"approved": repaired_approvals} for _ in repaired_rows]
                accepted, _, _ = self._screen_rows(
                    repaired_rows, repaired_approvals if isinstance(repaired_approvals, list) else [],
                    plan, snapshots, seeds, knowledge, seed_rejections, candidates_per_cycle,
                )
        return HighQualityResult(tuple(seeds), knowledge, tuple(accepted), seed_rejections, len(candidate_rows) + repaired_count)

    def _call_llm(self, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.llm.generate_json(**kwargs)
        except Exception as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        if not isinstance(response, dict):
            raise LLMUnavailable("invalid structured response")
        return response

    def _screen_rows(
        self,
        candidate_rows: list[Any],
        approvals: list[Any],
        plan: dict[str, Any],
        snapshots: LocalSnapshots,
        seeds: list[Any],
        knowledge: KnowledgeContext,
        rejections: dict[str, int],
        candidates_per_cycle: int,
    ) -> tuple[list[AcceptedCandidate], set[str], set[tuple[str, tuple[str, ...]]]]:
        accepted: list[AcceptedCandidate] = []
        used_behaviors: set[str] = set()
        used_pairs: set[tuple[str, tuple[str, ...]]] = set()
        for index, row in enumerate(candidate_rows):
            if len(accepted) >= min(5, max(1, int(candidates_per_cycle))):
                break
            if not isinstance(row, dict):
                _reject(rejections, "INVALID_LLM_CANDIDATE")
                continue
            approval = approvals[index] if index < len(approvals) and isinstance(approvals[index], dict) else {}
            if not approval.get("approved"):
                _reject(rejections, "LLM_CRITIQUE_REJECTED")
                continue
            outcome = self._validate_candidate(row, plan, snapshots, seeds, knowledge, used_behaviors, used_pairs)
            if isinstance(outcome, str):
                _reject(rejections, outcome)
                continue
            accepted.append(outcome)
            used_behaviors.add(behavior_signature(outcome.expression))
            used_pairs.add((operator_topology(outcome.expression), tuple(sorted(extract_fields(outcome.expression)))))
        return accepted, used_behaviors, used_pairs

    def _select_seeds(self, candidates: list[Any], feedback: FeedbackSummary) -> tuple[list[Any], dict[str, int]]:
        rejections: dict[str, int] = {}
        known_exact = {exact_hash(item.expression) for item in feedback.records if item.expression}
        known_structures = {structure_signature(item.expression) for item in feedback.records if item.expression}
        selected: list[Any] = []
        behavior_seen: set[str] = set()
        pair_seen: set[tuple[str, tuple[str, ...]]] = set()
        topology_seen: set[str] = set()
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
            topology = _function_topology(expression)
            if topology in topology_seen:
                _reject(rejections, "SEED_TOPOLOGY_DUPLICATE")
                continue
            selected.append(candidate)
            behavior_seen.add(behavior_signature(expression))
            pair_seen.add(pair)
            topology_seen.add(topology)
            if len(selected) >= 3:
                break
        return selected, rejections

    @staticmethod
    def _research_field_ids(snapshots: LocalSnapshots, seeds: list[Any]) -> set[str]:
        catalog_fields = list(snapshots.catalog.fields)
        visible = set(catalog_fields[:80])
        visible.update(
            field
            for seed in seeds
            for field in extract_fields(str(getattr(seed, "expression", "")))
            if field in snapshots.catalog.fields
        )
        return visible

    @staticmethod
    def _plan_issues(
        plan: dict[str, Any],
        snapshots: LocalSnapshots,
        allowed_fields: set[str],
        allowed_refs: set[str],
    ) -> tuple[str, ...]:
        issues: list[str] = []
        fields = _string_set(plan.get("fields_to_use"))
        operators = _string_set(plan.get("operators_to_use"))
        refs = _string_set(plan.get("knowledge_refs"))
        required_text = (
            "research_direction", "hypothesis", "economic_mechanism", "anti_correlation_plan",
            "expected_turnover_behavior",
        )
        if any(not str(plan.get(key) or "").strip() for key in required_text):
            issues.append("INVALID_RESEARCH_PLAN")
        if not fields or not fields <= allowed_fields:
            issues.append("PLAN_UNKNOWN_FIELD")
        if not operators or not operators <= set(snapshots.catalog.operators):
            issues.append("PLAN_UNKNOWN_OPERATOR")
        if operators & _GHOST_OPERATORS:
            issues.append("PLAN_GHOST_OPERATOR")
        datasets = {
            snapshots.catalog.fields[field].dataset_id
            for field in fields
            if field in snapshots.catalog.fields
        }
        if len(datasets) != 1:
            issues.append("PLAN_CROSS_DATASET")
        if not refs or not refs <= allowed_refs:
            issues.append("HALLUCINATED_KNOWLEDGE_REF")
        return tuple(dict.fromkeys(issues))

    @staticmethod
    def _canonicalize_plan_operators(plan: dict[str, Any], catalog_operators: dict[str, Any]) -> dict[str, Any]:
        """Normalize only documented arithmetic aliases; leave all other names hard-invalid."""

        normalized = dict(plan)
        raw = plan.get("operators_to_use")
        if isinstance(raw, (list, tuple, set)):
            operators: list[str] = []
            for value in raw:
                name = str(value).strip().lower()
                canonical = _PLAN_OPERATOR_ALIASES.get(name, name)
                operators.append(canonical if canonical in catalog_operators else name)
            normalized["operators_to_use"] = list(dict.fromkeys(operators))
        return normalized

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
        if feedback_refs <= {"none", "n/a", "na", "no_feedback", "no_feedback_available"}:
            feedback_refs = set()
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
        allowed_field_ids = HighQualityGenerator._research_field_ids(snapshots, seeds)
        payload = {
            "cycle_id": cycle_id,
            "catalog": {
                "datasets": sorted(catalog.datasets),
                "fields": [
                    {"id": catalog.fields[field].field_id, "dataset": catalog.fields[field].dataset_id,
                     "description": catalog.fields[field].description[:160]}
                    for field in sorted(allowed_field_ids)
                ],
                "operators": sorted(set(catalog.operators) - _GHOST_OPERATORS),
                "forbidden_identifiers": sorted(BASE_VARS - set(catalog.fields)),
            },
            "feedback": {
                "positive_refs": [item.ref_id for item in snapshots.feedback.positive[:12]],
                "near_pass_refs": [item.ref_id for item in snapshots.feedback.near_pass[:12]],
                "failure_counts": snapshots.feedback.failure_counts,
                "self_corr_refs": [item.ref_id for item in snapshots.feedback.self_corr_risk[:12]],
            },
            "knowledge": [{"ref_id": item.ref_id, "text": item.text} for item in knowledge.snippets],
            "v50_seeds": [str(getattr(item, "expression", "")) for item in seeds],
            "plan_requirements": [
                "Use only catalog.fields IDs, catalog.operators names, and knowledge ref IDs shown above.",
                "For arithmetic in an expression use + - * /; if listing operator names, use exact catalog names add/sub/mul/div.",
                "Choose fields from exactly one dataset.",
                "Parent seeds are structural inspiration; never reuse a catalog.forbidden_identifiers token.",
                "Avoid windows below 21, and use at least 42 for ts_corr.",
            ],
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
            "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
            "candidate_requirements": [
                "Produce at most one candidate per parent seed.",
                "Do not modify only a field, numeric window, sign, neutralization, or scalar.",
                "Do not use rank(ts_mean(volume,...)/adv...) as a generic cosmetic liquidity leg.",
                "Use only exact allowed IDs and include a specific economic rationale tied to the selected fields.",
                "Every selected field and operator must appear verbatim in the plan's fields_to_use and operators_to_use arrays.",
                "Do not use any forbidden_identifiers, even when a parent seed contains one.",
                "When allowed_feedback_refs is empty, set feedback_patterns_used to an empty JSON array, never a placeholder string.",
            ],
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


def _function_topology(expression: str) -> str:
    """Topology key that ignores field IDs and numeric fragments inside IDs."""
    functions = ">".join(extract_functions(expression)) or "raw"
    groups = ">".join(
        match.group(1).lower()
        for match in re.finditer(r"\b(?:group_rank|group_neutralize|group_mean|group_zscore)\([^,]+,\s*([a-z_]+)\s*\)", expression.lower())
    )
    return functions + "|" + groups


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
    return {
        "type": "object",
        "required": ["research_direction", "hypothesis", "economic_mechanism", "expected_horizon", "fields_to_use", "operators_to_use", "anti_correlation_plan", "expected_turnover_behavior", "historical_failures_to_avoid", "knowledge_refs"],
        "properties": {
            "research_direction": {"type": "string"}, "hypothesis": {"type": "string"},
            "economic_mechanism": {"type": "string"}, "expected_horizon": {"type": "string"},
            "fields_to_use": {"type": "array", "items": {"type": "string"}},
            "operators_to_use": {"type": "array", "items": {"type": "string"}},
            "anti_correlation_plan": {"type": "string"}, "expected_turnover_behavior": {"type": "string"},
            "historical_failures_to_avoid": {"type": "array", "items": {"type": "string"}},
            "knowledge_refs": {"type": "array", "items": {"type": "string"}},
        },
    }


def _candidate_schema() -> dict[str, Any]:
    return {
        "type": "object", "required": ["candidates"],
        "properties": {
            "candidates": {"type": "array", "maxItems": 5, "items": {
                "type": "object",
                "required": ["expression", "settings", "economic_rationale", "novelty_reason", "anti_corr_design", "parent_seed", "knowledge_refs", "feedback_patterns_used", "likely_failure_modes"],
                "properties": {
                    "expression": {"type": "string"}, "settings": {"type": "object"},
                    "economic_rationale": {"type": "string"}, "novelty_reason": {"type": "string"},
                    "anti_corr_design": {"type": "string"}, "parent_seed": {"type": "string"},
                    "knowledge_refs": {"type": "array", "items": {"type": "string"}},
                    "feedback_patterns_used": {"type": "array", "items": {"type": "string"}},
                    "likely_failure_modes": {"type": "array", "items": {"type": "string"}},
                },
            }},
        },
    }


def _critique_schema() -> dict[str, Any]:
    return {
        "type": "object", "required": ["approved"],
        "properties": {
            "approved": {"type": "array", "items": {
                "type": "object", "required": ["approved"],
                "properties": {"approved": {"type": "boolean"}, "critique": {"type": "string"}},
            }},
        },
    }
