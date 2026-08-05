"""Knowledge-grounded LLM refinement and deterministic local quality gates."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
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
from alpha_mining.generation.snapshots import LocalSnapshots
from alpha_mining.generation.portfolio import PortfolioLimits, select_candidates
from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.knowledge.worldquant_repository import KnowledgeIntent, KnowledgeContext, WorldQuantKnowledgeRepository


LOG = logging.getLogger(__name__)


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
    "mul": "multiply", "times": "multiply", "multiply": "multiply",
    "div": "divide", "division": "divide", "divide": "divide",
    "sub": "subtract", "minus": "subtract", "subtract": "subtract", "plus": "add",
}
_LOCALLY_GROUNDABLE_PLAN_ISSUES = frozenset({
    "PLAN_UNKNOWN_FIELD", "PLAN_UNKNOWN_OPERATOR", "PLAN_GHOST_OPERATOR",
    "PLAN_CROSS_DATASET", "HALLUCINATED_KNOWLEDGE_REF",
})
_REPAIRABLE_DRAFT_REJECTIONS = frozenset({
    "EMPTY_EXPRESSION", "INVALID_LLM_CANDIDATE", "CROSS_DATASET", "PLAN_SCOPE_VIOLATION",
    "UNKNOWN_FIELD", "UNKNOWN_OPERATOR", "GHOST_OPERATOR", "HALLUCINATED_KNOWLEDGE_REF",
    "HALLUCINATED_FEEDBACK_REF", "UNKNOWN_PARENT_SEED",
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
        offline_quality_threshold: float = 68.0,
        portfolio_mode: str = "enforce",
        portfolio_limits: PortfolioLimits | None = None,
        portfolio_pending_limit: int = 20,
    ) -> None:
        self.llm = llm
        self.kernel = kernel
        self.knowledge_repository = knowledge_repository or WorldQuantKnowledgeRepository()
        self.correlation_ceiling = float(correlation_ceiling)
        self.history_ceiling = float(history_ceiling)
        self.quality_threshold = float(quality_threshold)
        self.offline_quality_threshold = min(float(offline_quality_threshold), self.quality_threshold)
        self.portfolio_mode = str(portfolio_mode or "enforce").strip().lower()
        if self.portfolio_mode not in {"shadow", "enforce"}:
            raise ValueError("portfolio mode must be 'shadow' or 'enforce'")
        self.portfolio_limits = portfolio_limits or PortfolioLimits()
        self.portfolio_pending_limit = max(1, int(portfolio_pending_limit))

    def generate(self, snapshots: LocalSnapshots, *, cycle_id: str, candidates_per_cycle: int) -> HighQualityResult:
        raw_seeds = list(self.kernel.generate(snapshots))
        seeds, seed_rejections = self._select_seeds(raw_seeds, snapshots)
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
                if not set(repaired_plan_issues) <= _LOCALLY_GROUNDABLE_PLAN_ISSUES:
                    return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, 0)
                plan = self._locally_ground_plan(
                    plan, snapshots, seeds, allowed_plan_fields, allowed_refs,
                )
                grounded_field_scope = (
                    set(snapshots.catalog.fields)
                    if snapshots.catalog.info.get("source") == "local_offline_field_snapshot"
                    else allowed_plan_fields
                )
                grounded_plan_issues = self._plan_issues(plan, snapshots, grounded_field_scope, allowed_refs)
                if grounded_plan_issues:
                    for issue in grounded_plan_issues:
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
                "or platform behavior when the supplied catalog and evidence do not establish it. "
                "The exact field/operator scope is authoritative; narrative plan prose is not an extra requirement. "
                "If a candidate satisfies the exact scope and mechanism-evidence contract, set approved=true. Return JSON only."
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
        repaired_critic_all_approved = False
        if not accepted and candidate_rows and (all_critic_rejected or deterministic_draft_rejected):
            try:
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
                    "deterministic_rejections": dict(sorted(seed_rejections.items())),
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
                        "treat exact_expression_scope.fields and exact_expression_scope.operators as complete whitelists",
                        "do not copy any parent-seed token; a simple ts_rank or ts_zscore of one allowed field is preferable to an invalid composite",
                        "fix every listed deterministic rejection before returning a candidate",
                        "derive field_roles and operator_roles from the repaired expression exactly; do not retain roles for removed fields or operators",
                        "field_roles entries must use the keys field_id and role; operator_roles entries must use operator and role",
                    ],
                }, ensure_ascii=False),
                    json_schema=_candidate_schema(),
                )
            except LLMUnavailable:
                return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, len(candidate_rows))
            repaired_rows = repaired.get("candidates") if isinstance(repaired.get("candidates"), list) else []
            repaired_count = len(repaired_rows)
            if repaired_rows:
                try:
                    repaired_critique = self._call_llm(
                    system_prompt=(
                        "Audit repaired alpha candidates against only the supplied plan and exact scope. "
                        "Reject concrete hallucinations, scope violations, parent clones, absent mechanisms, "
                        "prohibited short windows, or explicit supplied correlation risks. Do not speculate "
                        "about coverage, lookahead, commonness, or platform behavior. The exact field/operator "
                        "scope is authoritative and narrative plan prose is not an extra requirement. "
                        "Approve candidates that satisfy the deterministic contract. Return one approval object per candidate."
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
                except LLMUnavailable:
                    return HighQualityResult(tuple(seeds), knowledge, (), seed_rejections, len(candidate_rows) + repaired_count)
                repaired_approvals = repaired_critique.get("approved") if isinstance(repaired_critique, dict) else []
                if isinstance(repaired_approvals, bool):
                    repaired_approvals = [{"approved": repaired_approvals} for _ in repaired_rows]
                repaired_critic_all_approved = (
                    len(repaired_approvals) == len(repaired_rows)
                    and all(
                        isinstance(approval, dict) and bool(approval.get("approved"))
                        for approval in repaired_approvals
                    )
                )
                accepted, _, _ = self._screen_rows(
                    repaired_rows, repaired_approvals if isinstance(repaired_approvals, list) else [],
                    plan, snapshots, seeds, knowledge, seed_rejections, candidates_per_cycle,
                )
        # The critique model is useful for explaining a concrete defect, but
        # it is not an authority on economic narrative.  A full batch can be
        # rejected merely because the model paraphrases the plan differently.
        # When *every initial draft* hits that condition, construct candidates
        # from the already validated plan and run the exact same deterministic
        # expression, mechanism, duplicate, correlation and quality gates.
        # This is deliberately narrower than the offline-catalog fallback:
        # malformed model drafts still do not receive a bypass.
        critique_only_exhaustion = bool(candidate_rows) and all_critic_rejected and not deterministic_draft_rejected
        # A complete catalog still needs a bounded deterministic escape hatch
        # when the model has gone through the repair pass but every repaired
        # row failed the same local gates.  The repair-count guard is
        # intentional: an initial malformed draft must keep the historical
        # fail-closed behavior and cannot receive a fallback without a repair
        # attempt first.
        repair_exhaustion = repaired_count > 0 and repaired_critic_all_approved
        if not accepted and (
            snapshots.catalog.info.get("source") == "local_offline_field_snapshot"
            or critique_only_exhaustion
            or repair_exhaustion
        ):
            fallback_rows = self._deterministic_fallback_rows(plan, snapshots, seeds, knowledge)
            if fallback_rows:
                fallback_approvals = [{"approved": True} for _ in fallback_rows]
                accepted, _, _ = self._screen_rows(
                    fallback_rows, fallback_approvals, plan, snapshots, seeds, knowledge,
                    seed_rejections, candidates_per_cycle,
                )
                if accepted:
                    _reject(seed_rejections, "DETERMINISTIC_LOCAL_FALLBACK_USED")
                    if critique_only_exhaustion:
                        _reject(seed_rejections, "LLM_CRITIQUE_RECOVERED_BY_DETERMINISTIC_GATES")
        accepted = self._select_portfolio(
            accepted,
            snapshots,
            candidates_per_cycle=candidates_per_cycle,
            rejections=seed_rejections,
        )
        return HighQualityResult(tuple(seeds), knowledge, accepted, seed_rejections, len(candidate_rows) + repaired_count)

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
        accepted_expressions: list[str] = []
        used_behaviors: set[str] = set()
        used_pairs: set[tuple[str, tuple[str, ...]]] = set()
        for index, row in enumerate(candidate_rows):
            if not isinstance(row, dict):
                _reject(rejections, "INVALID_LLM_CANDIDATE")
                continue
            approval = approvals[index] if index < len(approvals) and isinstance(approvals[index], dict) else {}
            if not approval.get("approved"):
                _reject(rejections, "LLM_CRITIQUE_REJECTED")
                critique = str(approval.get("critique") or "").strip()
                if critique:
                    LOG.info(
                        "candidate_critique_rejected expression=%s reason=%s",
                        str(row.get("expression") or "")[:180], critique[:240],
                    )
                continue
            outcome = self._validate_candidate(
                row,
                plan,
                snapshots,
                seeds,
                knowledge,
                used_behaviors,
                used_pairs,
                accepted_expressions,
            )
            if isinstance(outcome, str):
                _reject(rejections, outcome)
                LOG.debug(
                    "candidate_local_rejected expression=%s reason=%s fields=%s role_fields=%s",
                    str(row.get("expression") or "")[:180], outcome,
                    extract_fields(str(row.get("expression") or "")),
                    [item.get("field_id") for item in row.get("field_roles", []) if isinstance(item, dict)],
                )
                continue
            accepted.append(outcome)
            accepted_expressions.append(outcome.expression)
            used_behaviors.add(behavior_signature(outcome.expression))
            used_pairs.add((operator_topology(outcome.expression), tuple(sorted(extract_fields(outcome.expression)))))
        return accepted, used_behaviors, used_pairs

    def _select_portfolio(
        self,
        candidates: list[AcceptedCandidate],
        snapshots: LocalSnapshots,
        *,
        candidates_per_cycle: int,
        rejections: dict[str, int],
    ) -> tuple[AcceptedCandidate, ...]:
        if not candidates:
            return ()
        selection = select_candidates(
            candidates,
            inventory=snapshots.inventory.records,
            feedback=snapshots.feedback,
            limit=candidates_per_cycle,
            pending_limit=self.portfolio_pending_limit,
            limits=self.portfolio_limits,
            mode=self.portfolio_mode,
        )
        for reason, count in selection.rejection_counts.items():
            for _ in range(count):
                _reject(rejections, reason)
        decisions = {
            str(item.get("expression") or ""): item
            for item in selection.decisions
        }
        enriched: list[AcceptedCandidate] = []
        for candidate in selection.accepted:
            decision = decisions.get(candidate.expression, {})
            evidence = dict(candidate.quality_evidence)
            evidence["portfolio_selection"] = {
                "policy_version": self.portfolio_limits.policy_version,
                "mode": self.portfolio_mode,
                "inventory_hash": selection.inventory_hash,
                "decision": decision.get("decision", "ACCEPT"),
                "reason": decision.get("reason", "SELECTED"),
                "would_accept": bool(decision.get("would_accept", True)),
                "feedback_penalty": decision.get("feedback_penalty", {}),
                "occupancy": decision.get("occupancy", 0),
                "vector": decision.get("vector", {}),
            }
            enriched.append(replace(candidate, quality_evidence=evidence))
        return tuple(enriched)

    def _select_seeds(self, candidates: list[Any], snapshots: LocalSnapshots) -> tuple[list[Any], dict[str, int]]:
        rejections: dict[str, int] = {}
        known_expressions = [
            item.expression for item in snapshots.feedback.records if item.grounded and item.expression
        ] + list(snapshots.inventory.expressions)
        known_exact = {exact_hash(item) for item in known_expressions}
        known_structures = {structure_signature(item) for item in known_expressions}
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
        """Normalize documented arithmetic aliases to the local catalog spelling."""

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

    @staticmethod
    def _locally_ground_plan(
        plan: dict[str, Any],
        snapshots: LocalSnapshots,
        seeds: list[Any],
        allowed_fields: set[str],
        allowed_refs: set[str],
    ) -> dict[str, Any]:
        """Correct catalog-scope mistakes without inventing research inputs.

        The LLM has already supplied the research direction and mechanism. This
        narrow fallback only picks one allowed dataset and removes invalid names
        after the model's own repair pass still failed deterministic grounding.
        """

        grounded = HighQualityGenerator._canonicalize_plan_operators(plan, snapshots.catalog.operators)
        grounded["_locally_grounded"] = True
        requested_fields = [
            str(value).strip()
            for value in plan.get("fields_to_use", [])
            if isinstance(value, str) and str(value).strip() in allowed_fields
        ]
        seed_fields = [
            field
            for seed in seeds
            for field in extract_fields(str(getattr(seed, "expression", "")))
            if field in allowed_fields
        ]
        candidate_fields = requested_fields or seed_fields or sorted(allowed_fields)
        if candidate_fields:
            dataset_id = snapshots.catalog.fields[candidate_fields[0]].dataset_id
            fields = [
                field for field in candidate_fields
                if snapshots.catalog.fields[field].dataset_id == dataset_id
            ]
            if not fields:
                fields = [
                    field for field in sorted(allowed_fields)
                    if snapshots.catalog.fields[field].dataset_id == dataset_id
                ]
            same_dataset = [
                field for field in allowed_fields
                if snapshots.catalog.fields[field].dataset_id == dataset_id
            ]
            if snapshots.catalog.info.get("source") == "local_offline_field_snapshot":
                same_dataset = [
                    field for field, metadata in snapshots.catalog.fields.items()
                    if metadata.dataset_id == dataset_id
                ]
            ranked = sorted(
                same_dataset,
                key=lambda item: (-_field_quality_component((item,), snapshots), item),
            )
            grounded["fields_to_use"] = list(dict.fromkeys((ranked or fields)[:3]))

        operators = [
            str(value).strip().lower()
            for value in grounded.get("operators_to_use", [])
            if str(value).strip().lower() in snapshots.catalog.operators
            and str(value).strip().lower() not in _GHOST_OPERATORS
        ]
        if not operators:
            operators = [
                operator
                for seed in seeds
                for operator in extract_functions(str(getattr(seed, "expression", "")))
                if operator in snapshots.catalog.operators and operator not in _GHOST_OPERATORS
            ]
        if not operators:
            operators = [
                operator for operator in ("ts_rank", "rank")
                if operator in snapshots.catalog.operators
            ]
        grounded["operators_to_use"] = list(dict.fromkeys(operators))

        refs = [
            str(value).strip()
            for value in plan.get("knowledge_refs", [])
            if isinstance(value, str) and str(value).strip() in allowed_refs
        ]
        grounded["knowledge_refs"] = list(dict.fromkeys(refs)) or sorted(allowed_refs)[:1]
        return grounded

    def _validate_candidate(
        self,
        row: dict[str, Any],
        plan: dict[str, Any],
        snapshots: LocalSnapshots,
        seeds: list[Any],
        knowledge: KnowledgeContext,
        used_behaviors: set[str],
        used_pairs: set[tuple[str, tuple[str, ...]]],
        accepted_expressions: list[str],
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
        known_feedback_refs = {
            item.ref_id for item in snapshots.feedback.records if item.grounded and item.expression
        }
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
        history = [
            item.expression for item in snapshots.feedback.records if item.grounded and item.expression
        ]
        inventory = list(snapshots.inventory.expressions)
        existing = history + inventory
        if exact_hash(expression) in {exact_hash(item) for item in existing}:
            return "EXACT_DUPLICATE"
        normalized_hash = _hash(normalized_expression(expression))
        if normalized_hash in {_hash(normalized_expression(item)) for item in existing}:
            return "NORMALIZED_DUPLICATE"
        struct = structure_signature(expression)
        if struct in {structure_signature(item) for item in existing}:
            return "STRUCTURE_DUPLICATE"
        self_risk = max((_similarity(expression, item.expression) for item in snapshots.feedback.self_corr_risk if item.expression), default=0.0)
        if self_risk >= self.correlation_ceiling:
            return "SELF_CORRELATION_RISK"
        history_risk = max((_similarity(expression, item) for item in history), default=0.0)
        if history_risk >= self.history_ceiling:
            return "HISTORY_SIMILARITY"
        inventory_risk = max((_similarity(expression, item) for item in inventory), default=0.0)
        if inventory_risk >= self.history_ceiling:
            return "INVENTORY_SIMILARITY"
        cycle_risk = max((_similarity(expression, item) for item in accepted_expressions), default=0.0)
        if cycle_risk >= self.correlation_ceiling:
            return "CYCLE_SIMILARITY"
        behavior = behavior_signature(expression)
        pair = (operator_topology(expression), fields)
        if behavior in used_behaviors:
            return "BEHAVIOR_DUPLICATE"
        if pair in used_pairs:
            return "OPERATOR_FIELD_DUPLICATE"
        row = _complete_mechanism_roles(row, fields, functions)
        mechanism_issue = _mechanism_issue(row, expression, fields, functions, snapshots)
        if mechanism_issue:
            return mechanism_issue
        if _has_short_window(expression):
            return "SHORT_WINDOW"
        if _bare_price_expression(fields):
            return "BARE_PRICE_RISK"
        rationale = str(row.get("economic_rationale") or "").strip()
        anti = str(row.get("anti_corr_design") or "").strip()
        if len(rationale) < 20 or len(anti) < 12:
            return "WEAK_ECONOMIC_MECHANISM"
        score, evidence = self._quality_score(
            expression,
            fields,
            refs,
            feedback_refs,
            snapshots,
            max(self_risk, history_risk, inventory_risk, cycle_risk),
            mechanism_complete=True,
        )
        evidence["plan_locally_grounded"] = bool(plan.get("_locally_grounded"))
        evidence["mechanism_evidence_source"] = (
            "expression_parser_plus_llm_roles"
            if row.get("_mechanism_roles_completed")
            else "llm_declared_roles"
        )
        if score < self._quality_threshold(snapshots):
            return "LOW_LOCAL_QUALITY"
        settings = _settings(row.get("settings"), snapshots.catalog.info)
        return AcceptedCandidate(
            expression, settings, tuple(sorted(datasets)), parent, str(plan.get("research_direction") or ""), str(plan.get("hypothesis") or ""),
            rationale, tuple(sorted(refs)), tuple(sorted(feedback_refs)), anti,
            str(plan.get("expected_turnover_behavior") or ""), score,
            max(0.0, 1.0 - history_risk), self_risk, evidence,
            str(
                row.get("generator_source")
                or ("LLM_LOCALLY_GROUNDED_PLAN" if plan.get("_locally_grounded") else "LLM_REFINED_V50")
            ),
        )

    def _quality_threshold(self, snapshots: LocalSnapshots) -> float:
        if snapshots.catalog.info.get("source") == "local_offline_field_snapshot":
            return self.offline_quality_threshold
        return self.quality_threshold

    def _quality_score(
        self,
        expression: str,
        fields: tuple[str, ...],
        refs: set[str],
        feedback_refs: set[str],
        snapshots: LocalSnapshots,
        max_similarity: float,
        *,
        mechanism_complete: bool,
    ) -> tuple[float, dict[str, Any]]:
        field_component = _field_quality_component(fields, snapshots)
        referenced = [
            item for item in snapshots.feedback.records
            if item.ref_id in feedback_refs and item.grounded
        ]
        positive_support = sum(item in snapshots.feedback.positive for item in referenced)
        near_support = sum(item in snapshots.feedback.near_pass for item in referenced)
        feedback_component = min(20.0, positive_support * 10.0 + near_support * 6.0)
        novelty_component = max(0.0, 20.0 * (1.0 - min(1.0, max_similarity)))
        mechanism_component = 20.0 if mechanism_complete else 0.0
        knowledge_component = 10.0 if refs else 0.0
        risk_component = _risk_component(expression, max_similarity)
        value = (
            field_component + feedback_component + novelty_component
            + mechanism_component + knowledge_component + risk_component
        )
        evidence_cap = 100.0 if positive_support or near_support else 85.0
        value = min(value, evidence_cap)
        evidence = {
            "local_quality_score_definition": "local candidate ranking only; not platform Sharpe or Fitness",
            "generator_contract_version": "generation-hq-v2",
            "catalog_legal": True,
            "catalog_source": snapshots.catalog_source,
            "catalog_age_hours": round(snapshots.catalog_age_hours, 3),
            "knowledge_grounded": bool(refs),
            "field_count": len(fields),
            "feedback_refs": sorted(feedback_refs),
            "grounded_feedback_refs": sorted(item.ref_id for item in referenced),
            "positive_feedback_support": positive_support,
            "near_pass_support": near_support,
            "max_proxy_similarity": round(max_similarity, 4),
            "score_components": {
                "field_quality": round(field_component, 2),
                "grounded_feedback": round(feedback_component, 2),
                "novelty_low_similarity": round(novelty_component, 2),
                "mechanism_expression_consistency": round(mechanism_component, 2),
                "knowledge_relevance": round(knowledge_component, 2),
                "turnover_complexity_concentration_risk": round(risk_component, 2),
            },
            "evidence_cap": evidence_cap,
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
            "candidate_inventory": _inventory_prompt_summary(snapshots),
            "knowledge": [{"ref_id": item.ref_id, "text": item.text} for item in knowledge.snippets],
            "v50_seeds": [str(getattr(item, "expression", "")) for item in seeds],
            "plan_requirements": [
                "Use only catalog.fields IDs, catalog.operators names, and knowledge ref IDs shown above.",
                "For arithmetic in an expression use + - * /; if listing operator names, copy the exact spelling from catalog.operators.",
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
            "allowed_fields": sorted(_string_set(plan.get("fields_to_use"))),
            "allowed_operators": sorted(_string_set(plan.get("operators_to_use"))),
            "allowed_knowledge_refs": [item.ref_id for item in knowledge.snippets],
            "allowed_feedback_refs": [
                item.ref_id for item in snapshots.feedback.records if item.grounded and item.expression
            ],
            "allowed_parent_seeds": [str(getattr(item, "expression", "")) for item in seeds],
            "candidate_inventory": _inventory_prompt_summary(snapshots),
            "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
            "candidate_requirements": [
                "Produce at most one candidate per parent seed.",
                "Do not modify only a field, numeric window, sign, neutralization, or scalar.",
                "Do not use rank(ts_mean(volume,...)/adv...) as a generic cosmetic liquidity leg.",
                "Use only exact allowed IDs and include a specific economic rationale tied to the selected fields.",
                "Every selected field and operator must appear verbatim in the plan's fields_to_use and operators_to_use arrays.",
                "Treat plan.fields_to_use and plan.operators_to_use as complete whitelists; do not use any other token from v50_seeds or candidate_inventory.",
                "Parent seeds are mechanism context only. Never copy cap, volume, adv20, returns, market, sector, or any other parent token unless it is explicitly in the plan whitelist.",
                "When a combination cannot be proven legal, use one exact allowed field with one exact allowed time-series operator and a window of 63 or 126.",
                "Provide field_roles for every field used and operator_roles for every function used in the expression.",
                "field_roles must contain exactly the expression's extracted fields, and operator_roles exactly its extracted functions: no missing or extra entries.",
                "Use field_roles objects with field_id and role keys, and operator_roles objects with operator and role keys.",
                "turnover_controls and correlation_diversifiers must name only fields/operators actually present in the expression.",
                "Do not use any forbidden_identifiers, even when a parent seed contains one.",
                "Avoid repeating candidate_inventory used research directions, field sets, and operator topologies unless grounded feedback supports a material repair.",
                "Use recent_rejection_counts to change the mechanism rather than making a cosmetic clone.",
                "When allowed_feedback_refs is empty, set feedback_patterns_used to an empty JSON array, never a placeholder string.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _deterministic_fallback_rows(
        plan: dict[str, Any], snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext,
    ) -> list[dict[str, Any]]:
        """Build a minimal legal candidate after LLM drafts exhaust their repair pass."""

        fields = [
            field for field in _string_set(plan.get("fields_to_use"))
            if field in snapshots.catalog.fields
        ]
        operators = [
            operator for operator in _string_set(plan.get("operators_to_use"))
            if operator in snapshots.catalog.operators and operator not in _GHOST_OPERATORS
        ]
        refs = sorted({item.ref_id for item in knowledge.snippets} & _string_set(plan.get("knowledge_refs")))
        parents = [str(getattr(seed, "expression", "")) for seed in seeds if str(getattr(seed, "expression", ""))]
        if not fields or not operators or not refs or not parents:
            return []
        priority = ("ts_rank", "ts_zscore", "ts_mean", "ts_delta", "rank")
        ordered_operators = sorted(operators, key=lambda item: (priority.index(item) if item in priority else len(priority), item))
        rows: list[dict[str, Any]] = []
        ranked_fields = sorted(fields, key=lambda item: (-_field_quality_component((item,), snapshots), item))[:3]
        for field in ranked_fields:
            for operator in ordered_operators[:3]:
                arity = int(getattr(snapshots.catalog.operators[operator], "arity", -1))
                if arity == 1:
                    expression = f"{operator}({field})"
                elif arity == 2 and operator.startswith("ts_"):
                    expression = f"{operator}({field},126)"
                else:
                    continue
                functions = extract_functions(expression)
                rows.append({
                    "expression": expression,
                    "settings": {},
                    "economic_rationale": f"A slow {operator} transform of {field} captures persistent information diffusion.",
                    "novelty_reason": "A minimal locally grounded expression used after invalid model drafts.",
                    "anti_corr_design": f"The single-field {operator} signal avoids unsupported cross-dataset and group operators.",
                    "parent_seed": parents[0],
                    "knowledge_refs": refs,
                    "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
                    "field_roles": [{"field_id": field, "role": "economic input"}],
                    "operator_roles": [{"operator": function, "role": "signal transformation"} for function in functions],
                    "turnover_controls": functions[:1],
                    "correlation_diversifiers": [field],
                    "generator_source": "DETERMINISTIC_LOCAL_FALLBACK",
                })
        return rows


def _inventory_prompt_summary(snapshots: LocalSnapshots) -> dict[str, Any]:
    directions = sorted({
        item.research_direction for item in snapshots.inventory.records if item.research_direction
    })[:24]
    field_sets = sorted({
        tuple(sorted(item.data_fields)) for item in snapshots.inventory.records if item.data_fields
    })[:24]
    topologies = sorted({
        operator_topology(item.expression)
        for item in snapshots.inventory.records
        if item.expression
    })[:24]
    rejection_counts = dict(snapshots.inventory.rejection_counts)
    return {
        "used_research_directions": directions,
        "used_field_sets": [list(items) for items in field_sets],
        "used_operator_topologies": topologies,
        "recent_rejection_counts": dict(sorted(rejection_counts.items())),
    }


def revalidate_pending_rows(
    rows: list[dict[str, str]],
    snapshots: LocalSnapshots,
) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """Quarantine legacy pending rows that cannot prove the v2 quality contract.

    Old rows are retained verbatim apart from status/error fields.  Consumer
    owned terminal states are never touched, and no row is deleted.
    """

    del snapshots  # The v2 evidence marker is the deterministic revalidation boundary.
    updated: list[dict[str, str]] = []
    changes: list[tuple[str, str]] = []
    for source in rows:
        row = dict(source)
        if row.get("queue_status") != "PENDING_SIMULATION":
            updated.append(row)
            continue
        try:
            evidence = json.loads(row.get("quality_evidence_json") or "{}")
        except json.JSONDecodeError:
            evidence = {}
        version = evidence.get("generator_contract_version") if isinstance(evidence, dict) else None
        if version != "generation-hq-v2":
            row["queue_status"] = "REJECTED_LOCAL_REVALIDATION"
            row["last_error_category"] = "LEGACY_CONTRACT_MISSING_EVIDENCE"
            row["last_error"] = "candidate lacks generation-hq-v2 deterministic quality evidence"
            changes.append((str(row.get("candidate_id") or ""), row["last_error_category"]))
        updated.append(row)
    return updated, changes


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


def _mechanism_issue(
    row: dict[str, Any],
    expression: str,
    fields: tuple[str, ...],
    functions: set[str],
    snapshots: LocalSnapshots,
) -> str:
    field_roles = row.get("field_roles")
    operator_roles = row.get("operator_roles")
    turnover_controls = _string_set(row.get("turnover_controls"))
    diversifiers = _string_set(row.get("correlation_diversifiers"))
    if not isinstance(field_roles, list) or not isinstance(operator_roles, list):
        return "MECHANISM_EVIDENCE_MISSING"
    claimed_fields = {
        str(item.get("field_id") or "").strip()
        for item in field_roles if isinstance(item, dict) and str(item.get("role") or "").strip()
    }
    claimed_operators = {
        str(item.get("operator") or "").strip().lower()
        for item in operator_roles if isinstance(item, dict) and str(item.get("role") or "").strip()
    }
    if claimed_fields != set(fields):
        return "MECHANISM_FIELD_MISMATCH"
    if claimed_operators != functions:
        return "MECHANISM_OPERATOR_MISMATCH"
    expression_items = set(fields) | functions
    if not turnover_controls or not turnover_controls <= expression_items:
        return "TURNOVER_CONTROL_MISMATCH"
    if not diversifiers or not diversifiers <= expression_items:
        return "ANTI_CORR_DESIGN_MISMATCH"
    rationale = str(row.get("economic_rationale") or "")
    mentioned_catalog_fields = {
        field for field in snapshots.catalog.fields
        if re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", rationale, flags=re.IGNORECASE)
    }
    if not mentioned_catalog_fields <= set(fields):
        return "MECHANISM_FIELD_MISMATCH"
    return ""


def _complete_mechanism_roles(row: dict[str, Any], fields: tuple[str, ...], functions: set[str]) -> dict[str, Any]:
    """Fill only omitted structural role entries; never remove or rewrite claims."""

    completed = dict(row)
    changed = False
    for key, identity_key, expected in (
        ("field_roles", "field_id", set(fields)),
        ("operator_roles", "operator", set(functions)),
    ):
        raw = completed.get(key)
        if not isinstance(raw, list):
            continue
        entries = [item for item in raw if isinstance(item, dict)]
        claimed = {str(item.get(identity_key) or "").strip().lower() for item in entries}
        if claimed <= {value.lower() for value in expected}:
            missing = expected - claimed
            completed[key] = entries + [
                {identity_key: value, "role": "deterministically extracted from expression"}
                for value in sorted(missing)
            ]
            changed = changed or bool(missing)
    if changed:
        completed["_mechanism_roles_completed"] = True
    return completed


def _field_quality_component(fields: tuple[str, ...], snapshots: LocalSnapshots) -> float:
    if not fields:
        return 0.0
    scores: list[float] = []
    for field_id in fields:
        field = snapshots.catalog.fields[field_id]
        coverage = getattr(field, "coverage", None)
        date_coverage = getattr(field, "date_coverage", None)
        user_count = getattr(field, "user_count", None)
        coverage_score = 0.75 if coverage is None else max(0.0, min(1.0, float(coverage)))
        date_score = 0.75 if date_coverage is None else max(0.0, min(1.0, float(date_coverage)))
        if user_count is None:
            crowding_score = 0.75
        else:
            crowding_score = 1.0 / (1.0 + max(0.0, float(user_count)) / 100.0)
        scores.append((coverage_score * 0.45 + date_score * 0.35 + crowding_score * 0.20) * 20.0)
    return sum(scores) / len(scores)


def _risk_component(expression: str, max_similarity: float) -> float:
    functions = extract_functions(expression)
    complexity_penalty = max(0, len(functions) - 5) * 1.25
    similarity_penalty = min(6.0, max_similarity * 6.0)
    return max(0.0, 10.0 - complexity_penalty - similarity_penalty)


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
                "required": [
                    "expression", "settings", "economic_rationale", "novelty_reason", "anti_corr_design",
                    "parent_seed", "knowledge_refs", "feedback_patterns_used", "likely_failure_modes",
                    "field_roles", "operator_roles", "turnover_controls", "correlation_diversifiers",
                ],
                "properties": {
                    "expression": {"type": "string"}, "settings": {"type": "object"},
                    "economic_rationale": {"type": "string"}, "novelty_reason": {"type": "string"},
                    "anti_corr_design": {"type": "string"}, "parent_seed": {"type": "string"},
                    "knowledge_refs": {"type": "array", "items": {"type": "string"}},
                    "feedback_patterns_used": {"type": "array", "items": {"type": "string"}},
                    "likely_failure_modes": {"type": "array", "items": {"type": "string"}},
                    "field_roles": {"type": "array", "items": {"type": "object", "required": ["field_id", "role"]}},
                    "operator_roles": {"type": "array", "items": {"type": "object", "required": ["operator", "role"]}},
                    "turnover_controls": {"type": "array", "items": {"type": "string"}},
                    "correlation_diversifiers": {"type": "array", "items": {"type": "string"}},
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
