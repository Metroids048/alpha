"""Knowledge-grounded LLM refinement and deterministic local quality gates."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
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
from alpha_mining.domain.expression_ast import AstNode, ExpressionSyntaxError, parse_expression
from alpha_mining.domain.operator_registry import BASE_VARS, GROUPS
from alpha_mining.generation.snapshots import LocalSnapshots
from alpha_mining.generation.portfolio import PortfolioLimits, select_candidates
from alpha_mining.generation.validation import LocalExpressionValidator, ValidationIssue
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
    context_refs: tuple[str, ...] = ()
    knowledge_context_hash: str = ""
    degraded: bool = False


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
# Ceiling for the serialized ``fields_by_dataset`` block, in characters.  The
# whole research prompt has to fit a 64k-token context (~4 chars/token, so
# ~256k chars) with room left for the operator list, knowledge, seeds,
# plan_requirements and the model's own reply.  See _research_field_ids.
_RESEARCH_FIELD_CHAR_BUDGET = 150_000
# JSON scaffolding per field entry: {"id": "", "description": "", "field_type":
# ""} plus the comma, i.e. what a field costs beyond its id, description and
# type text.
_RESEARCH_FIELD_ENTRY_OVERHEAD = 58
# Admitted regardless of budget: below three datasets the concentration gate
# has nothing to choose between.
_RESEARCH_MIN_DATASETS = 3
# Reducers that collapse a per-instrument event stream to one value per day.
# A VECTOR field is only legal as their immediate argument: every other
# operator refuses it ("does not support event inputs").
_VECTOR_REDUCER_PREFIX = "vec_"


def _group_axis_identifiers(expression: str) -> set[str]:
    """Grouping keywords used as the *axis* argument of a ``group_*`` call.

    ``sector``, ``industry``, ``subindustry``, ``market`` and ``country`` are
    partition axes, but on the live catalog they are also real ``pv1`` fields.
    Position is what separates the two readings, so it is read off the AST
    rather than guessed from the name: only a ``GROUPS`` identifier sitting in
    a non-first argument of a ``group_*`` call is an axis.  The same token used
    as an ordinary operand stays a data draw and stays subject to the
    single-dataset rule.
    """

    try:
        root = parse_expression(expression)
    except ExpressionSyntaxError:
        return set()

    axes: set[str] = set()

    def walk(node: AstNode) -> None:
        if node.kind == "call" and str(node.value or "").lower().startswith("group_"):
            for child in node.children[1:]:
                if child.kind == "ident" and str(child.value) in GROUPS:
                    axes.add(str(child.value))
        for child in node.children:
            walk(child)

    walk(root)
    return axes


def _suppressible_scope_issue(issue: ValidationIssue, group_axes: set[str]) -> bool:
    """True when a scope issue is an artefact of a group axis, not a real fault.

    The validator has no notion of an axis: it sees an identifier, looks it up,
    and reports either UNKNOWN_FIELD or -- once the catalog does carry
    ``sector`` as a ``pv1`` field -- FIELD_DATASET_MISMATCH.  Both are false
    here.  Suppression is keyed on the identifier actually occupying an axis
    position in this very expression, so a genuine second dataset, and a group
    keyword used as an operand, both keep failing.
    """

    if issue.code == "UNKNOWN_FIELD":
        return issue.message in GROUPS and issue.message in group_axes
    if issue.code == "FIELD_DATASET_MISMATCH":
        return str(issue.message).split(" ", 1)[0] in group_axes
    return False


def _unreduced_vector_fields(expression: str, catalog: Any) -> tuple[str, ...]:
    """VECTOR fields not sitting directly inside a ``vec_*`` reducer.

    A VECTOR field is an event stream: several records per instrument per day,
    not one value. Every operator except the ``vec_*`` reducers refuses it, which
    the platform reports as "Operator <name> does not support event inputs". The
    reducer is what collapses the stream, so the check is positional and
    *immediate* -- a reducer higher up the tree does not help, because the
    operator between them is the one that receives the raw stream.

    Read off the AST rather than by name: ``field_type`` is the only thing that
    distinguishes the two, and 16144 of the live catalog's fields are VECTOR, so
    this cannot be a hardcoded identifier list.
    """

    try:
        root = parse_expression(expression)
    except ExpressionSyntaxError:
        return ()

    fields = getattr(catalog, "fields", {}) or {}

    def is_vector(name: str) -> bool:
        field = fields.get(name)
        return str(getattr(field, "field_type", "") or "").upper() == "VECTOR"

    unreduced: list[str] = []

    def walk(node: AstNode, reduced: bool) -> None:
        if node.kind == "ident":
            name = str(node.value)
            if not reduced and is_vector(name) and name not in unreduced:
                unreduced.append(name)
            return
        is_reducer = node.kind == "call" and str(
            node.value or ""
        ).lower().startswith(_VECTOR_REDUCER_PREFIX)
        for child in node.children:
            walk(child, is_reducer)

    walk(root, False)
    return tuple(unreduced)


_REPAIRABLE_DRAFT_REJECTIONS = frozenset({
    "EMPTY_EXPRESSION", "INVALID_LLM_CANDIDATE", "CROSS_DATASET", "PLAN_SCOPE_VIOLATION",
    "UNKNOWN_FIELD", "UNKNOWN_OPERATOR", "GHOST_OPERATOR", "HALLUCINATED_KNOWLEDGE_REF",
    "HALLUCINATED_FEEDBACK_REF", "UNKNOWN_PARENT_SEED",
    "LOCAL_VALIDATION_UNKNOWN_FIELD",
    "LOCAL_VALIDATION_UNKNOWN_OPERATOR", "LOCAL_VALIDATION_INVALID_ARITY", "LOCAL_VALIDATION_FASTPLUS",
    "LOCAL_VALIDATION_INVALID_SYNTAX", "SHORT_WINDOW", "BARE_PRICE_RISK", "WEAK_ECONOMIC_MECHANISM",
    "DEGENERATE_SHAPE",
    # A role table that names an operator or field its own expression never uses is
    # a false claim, so the strict gate must keep refusing it.  But the defect is
    # clerical, not a research failure: the model copied the plan whitelist into
    # operator_roles instead of reading its own expression.  Measured on a live
    # cycle this killed 5 of 5 candidates with no repair pass, so route it through
    # the one correction pass that already instructs an exact re-derivation.
    "MECHANISM_OPERATOR_MISMATCH", "MECHANISM_FIELD_MISMATCH", "MECHANISM_EVIDENCE_MISSING",
    # A missing vec_* wrapper is mechanical, not a research failure: the
    # hypothesis and topology are intact, one operand just needs reducing.  The
    # payload now carries field_type, so the repair pass can see what to wrap.
    "VECTOR_FIELD_NOT_REDUCED",
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
        offline_quality_threshold: float = 75.0,
        portfolio_mode: str = "enforce",
        portfolio_limits: PortfolioLimits | None = None,
        portfolio_pending_limit: int = 20,
        settings_contract: Any | None = None,
        allow_degraded: bool = False,
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
        self.settings_contract = settings_contract
        self.allow_degraded = bool(allow_degraded)

    def generate(self, snapshots: LocalSnapshots, *, cycle_id: str, candidates_per_cycle: int) -> HighQualityResult:
        raw_seeds = list(self.kernel.generate(snapshots))

        # 【新增】阶段3: 熔断风险预过滤（在LLM调用之前）
        from alpha_mining.generation.circuit_filter import filter_seeds_by_circuit_risk
        circuit_safe_seeds, circuit_rejections = filter_seeds_by_circuit_risk(
            raw_seeds,
            snapshots,
            risk_threshold=0.5,
            max_high_risk_seeds=2,  # 允许最多2个高风险种子探索性通过
        )

        seeds, seed_rejections = self._select_seeds(circuit_safe_seeds, snapshots)

        # 合并熔断拒绝统计
        for reason, count in circuit_rejections.items():
            seed_rejections[reason] = seed_rejections.get(reason, 0) + count
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
                        # Same grouping as the research prompt: this repair runs
                        # precisely when the flat view let a cross-dataset mix
                        # through, so it must not restate the scope flatly.
                        "fields_by_dataset": _fields_by_dataset_scope(snapshots, allowed_plan_fields),
                        "operators": sorted(set(snapshots.catalog.operators) - _GHOST_OPERATORS),
                        "forbidden_identifiers": sorted(BASE_VARS - set(snapshots.catalog.fields)),
                        "knowledge_refs": sorted(allowed_refs),
                    },
                    "requirements": [
                        "fields_by_dataset is keyed by dataset: pick exactly ONE key and take every "
                        "field in fields_to_use from that key's list only",
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
                "CRITICAL: Judge the candidate expression ONLY. A parent_seed may contain fields or operators outside "
                "the current scope — this is expected and must NEVER cause rejection. Only reject if the candidate "
                "expression itself uses a disallowed identifier. "
                "CRITICAL: Do NOT require group_neutralize, orthogonalization, or any structural element that is not "
                "present in the plan's allowed operators. If the candidate uses only allowed fields and operators and "
                "has a clear economic mechanism, set approved=true. Return JSON only."
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
                        "do not copy any parent-seed token; when a composite cannot be made legal, "
                        "choose a different pair of allowed fields rather than collapsing to one field, "
                        "because a single allowed field wrapped in a single non-grouping operator is "
                        "refused by a shape gate",
                        "fix every listed deterministic rejection before returning a candidate",
                        "derive field_roles and operator_roles from the repaired expression exactly; do not retain roles for removed fields or operators",
                        "field_roles entries must use the keys field_id and role; operator_roles entries must use operator and role",
                        "CRITICAL – economic_rationale MUST NOT mention any catalog field name outside allowed_fields. Use generic economic terms only.",
                        "CRITICAL – every function call in the expression must exactly match an entry in exact_expression_scope.operators. No other operators allowed.",
                        "CRITICAL – all fields in the repaired expression must belong to the same dataset. Never combine fields from different datasets.",
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
                        "CRITICAL: Judge the candidate expression ONLY, not its parent_seed. Parent seeds may "
                        "contain out-of-scope identifiers — that is expected and never a rejection reason. "
                        "CRITICAL: Do NOT require group_neutralize or orthogonalization unless it is in "
                        "exact_expression_scope.operators. Approve candidates that use only scope identifiers "
                        "and have a clear mechanism. Return one approval object per candidate."
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
        # The deterministic escape hatch is intentionally opt-in.  Even when
        # the critic rejects only narrative or repaired rows exhaust local gates,
        # normal production remains fail-closed unless the operator explicitly
        # accepts a single degraded candidate for later platform verification.
        critique_only_exhaustion = bool(candidate_rows) and all_critic_rejected and not deterministic_draft_rejected
        # A repair attempt is still required before repair exhaustion can
        # qualify for the explicit degraded path.
        repair_exhaustion = repaired_count > 0 and repaired_critic_all_approved
        all_deterministic_rejected = bool(candidate_rows) and not accepted and not all_critic_rejected

        # 阶段2诊断：打印关键变量
        import sys
        print(f"[PRE_DEGRADED] allow_degraded={self.allow_degraded} | accepted={len(accepted)} | candidate_rows={len(candidate_rows)} | all_critic_rejected={all_critic_rejected} | all_deterministic_rejected={all_deterministic_rejected} | offline={snapshots.catalog.info.get('source')}", file=sys.stderr)

        # 阶段2诊断：打印降级触发条件
        degraded_trigger = None
        if self.allow_degraded and not accepted:
            if snapshots.catalog.info.get("source") == "local_offline_field_snapshot":
                degraded_trigger = "offline_catalog"
            elif critique_only_exhaustion:
                degraded_trigger = "critique_exhaustion"
            elif repair_exhaustion:
                degraded_trigger = "repair_exhaustion"
            elif all_deterministic_rejected:
                degraded_trigger = "all_deterministic_rejected"

        if degraded_trigger:
            import sys
            print(f"[DEGRADED_TRIGGER] {degraded_trigger} | candidates={len(candidate_rows)} accepted={len(accepted)} all_critic_rejected={all_critic_rejected}", file=sys.stderr)

        if self.allow_degraded and not accepted and (
            snapshots.catalog.info.get("source") == "local_offline_field_snapshot"
            or critique_only_exhaustion
            or repair_exhaustion
            or all_deterministic_rejected  # 阶段2: 候选全被确定性gate拒绝时也降级兜底
        ):
            # 阶段2: 生成更多兜底候选池 + 过滤已用过的
            all_fallback = self._deterministic_fallback_rows(plan, snapshots, seeds, knowledge)
            # 排除所有已知表达式（含FAR_FAIL），不允许重试已失败的相同表达式
            history_exprs = {
                exact_hash(item.expression)
                for item in snapshots.feedback.records
                if item.grounded and item.expression
            }
            inventory_exprs = {exact_hash(expr) for expr in snapshots.inventory.expressions}
            existing_hashes = history_exprs | inventory_exprs
            fresh_fallback = [
                row for row in all_fallback
                if exact_hash(row.get("expression", "")) not in existing_hashes
            ]
            fallback_rows = fresh_fallback[:3]  # 取前3个未用过的
            print(f"[DEGRADED_FALLBACK] pool={len(all_fallback)} fresh={len(fresh_fallback)} selected={len(fallback_rows)} | first_expr={fallback_rows[0]['expression'] if fallback_rows else None}", file=sys.stderr)
            if fallback_rows:
                fallback_approvals = [{"approved": True} for _ in fallback_rows]
                fallback_rejections_before = dict(seed_rejections)  # 快照
                accepted_fallback, _, _ = self._screen_rows(
                    fallback_rows, fallback_approvals, plan, snapshots, seeds, knowledge,
                    seed_rejections, candidates_per_cycle,
                )
                fallback_new_rejections = {k: seed_rejections[k] - fallback_rejections_before.get(k, 0) for k in seed_rejections if seed_rejections[k] > fallback_rejections_before.get(k, 0)}
                print(f"[DEGRADED_SCREEN] accepted={len(accepted_fallback)} from {len(fallback_rows)} fallback rows", file=sys.stderr)
                if fallback_new_rejections:
                    print(f"[DEGRADED_REJECT] fallback rejected by: {fallback_new_rejections}", file=sys.stderr)
                if accepted_fallback:
                    accepted = accepted_fallback
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
        catalog = getattr(snapshots, "catalog", None)
        catalog_fields = getattr(catalog, "fields", {})
        selection = select_candidates(
            candidates,
            inventory=snapshots.inventory.records,
            feedback=snapshots.feedback,
            limit=candidates_per_cycle,
            pending_limit=self.portfolio_pending_limit,
            limits=self.portfolio_limits,
            mode=self.portfolio_mode,
            eligible_dataset_count=len({field.dataset_id for field in catalog_fields.values()}),
            eligible_field_count=len(catalog_fields),
        )
        for reason, count in selection.rejection_counts.items():
            for _ in range(count):
                _reject(rejections, reason)
        # 阶段2: 打印portfolio拒绝原因
        if selection.rejection_counts:
            print(f"[PORTFOLIO_REJECT] {selection.rejection_counts} | accepted={len(selection.accepted)}/{len(candidates)}", file=sys.stderr)
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
        topology_seen: set[str] = set()  # 空集开始,只防止本轮种子内拓扑重复,不排除历史(已有exact/structure去重)
        # A topology key deliberately ignores field IDs, so v50 candidates that
        # study different fields through the same operator skeleton collapse onto
        # one key.  Prefer topological spread, but rather than discard a
        # field-distinct row outright, defer it and backfill any seed slot the
        # first pass could not fill.  On a full candidate pool the first pass
        # fills the budget and nothing is backfilled; this only matters when v50
        # returns few distinct topologies, where an empty slot would otherwise
        # cost the plan a real study.
        deferred: list[Any] = []
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
                deferred.append(candidate)
                continue
            selected.append(candidate)
            behavior_seen.add(behavior_signature(expression))
            pair_seen.add(pair)
            topology_seen.add(topology)
            if len(selected) >= 5:  # 阶段2修正: 5个种子配合candidates_per_cycle=5，保持2-3倍变体率
                break
        # Backfill: a deferred row already cleared the exact, structure, behavior
        # and operator/field-pair gates, so it is a genuinely different study that
        # merely reuses an operator skeleton.  It is better research input than an
        # empty seed slot.
        for candidate in deferred:
            if len(selected) >= 5:
                # Every remaining deferred row is genuinely dropped for topology.
                _reject(rejections, "SEED_TOPOLOGY_DUPLICATE")
                continue
            expression = str(getattr(candidate, "expression", "") or "").strip()
            fields = tuple(sorted(extract_fields(expression)))
            pair = (operator_topology(expression), fields)
            if behavior_signature(expression) in behavior_seen or pair in pair_seen:
                _reject(rejections, "SEED_DIVERSITY_DUPLICATE")
                continue
            selected.append(candidate)
            behavior_seen.add(behavior_signature(expression))
            pair_seen.add(pair)
        return selected, rejections

    @staticmethod
    def _research_field_ids(
        snapshots: LocalSnapshots,
        seeds: list[Any],
        *,
        per_dataset: int = 40,
        char_budget: int = _RESEARCH_FIELD_CHAR_BUDGET,
    ) -> set[str]:
        """Expose a bounded, rotating window of deep per-dataset views.

        Two failure modes have to be avoided at once.

        A plain ``catalog_fields[:2000]`` is not a wider view, it is a biased one:
        measured on the live catalog it surfaced 1828 analyst10 fields (91.4% of
        what the model could see) and left analyst15 (2556 fields) and analyst14
        (856 fields) completely unreachable, i.e. 59.9% of the catalog. So the
        quota is taken *per dataset*, ranked within the dataset.

        But a quota alone is unbounded in the number of datasets. On the live
        297-dataset catalog, 40 fields each is 8680 fields and a 1.21M-char
        prompt -- roughly 303k tokens against DeepSeek's 64k context, with
        ``fields_by_dataset`` at 99.2% of the payload. Everything serialized
        after it (``knowledge``, ``v50_seeds``, ``plan_requirements``) was cut
        off by the endpoint, which is what surfaced as HALLUCINATED_KNOWLEDGE_REF.
        So the datasets are also capped by a character budget.

        Capping *datasets* rather than shrinking the per-dataset quota is the
        deliberate choice: a 3-field view of 297 datasets fits the budget too,
        but no alpha can be built from it. Depth is kept and breadth is spread
        across cycles -- the window is ordered by active pending occupancy, so
        each cycle's work pushes its datasets down the order and surfaces the
        next ones.
        """

        by_dataset: dict[str, list[str]] = {}
        for field_id, field in snapshots.catalog.fields.items():
            by_dataset.setdefault(str(getattr(field, "dataset_id", "") or ""), []).append(field_id)

        def rank(field_id: str) -> tuple[float, str]:
            field = snapshots.catalog.fields[field_id]
            described = 1.0 if str(getattr(field, "description", "") or "").strip() else 0.0
            quality = _field_quality_component((field_id,), snapshots)
            return (-(quality + described), field_id)

        pending: dict[str, int] = {dataset: 0 for dataset in by_dataset}
        for item in snapshots.inventory.records:
            if item.queue_status == "PENDING_SIMULATION" and item.dataset in pending:
                pending[str(item.dataset)] += 1
        order = sorted(by_dataset, key=lambda dataset: (pending[dataset], dataset))

        visible: set[str] = set()
        spent = 0
        for position, dataset in enumerate(order):
            chosen = sorted(by_dataset[dataset], key=rank)[: max(1, per_dataset)]
            cost = sum(
                _RESEARCH_FIELD_ENTRY_OVERHEAD
                + len(field_id)
                + len(str(getattr(snapshots.catalog.fields[field_id], "description", "") or "")[:160])
                for field_id in chosen
            )
            # The concentration gate needs a real choice, so the first few
            # datasets are admitted even if the budget is already spent.
            if spent + cost > char_budget and position >= _RESEARCH_MIN_DATASETS:
                break
            visible.update(chosen)
            spent += cost
        # Seed fields must stay in scope or the plan cannot reference its own
        # parents -- their dataset may well sit outside the current window.
        visible.update(
            field
            for seed in seeds
            for field in extract_fields(str(getattr(seed, "expression", "")))
            if field in snapshots.catalog.fields
        )
        return visible

    @staticmethod
    def _dataset_occupancy(
        snapshots: LocalSnapshots,
        allowed_fields: set[str],
    ) -> dict[str, int]:
        """Active pending candidate count per viable dataset."""

        viable = {
            str(snapshots.catalog.fields[field].dataset_id)
            for field in allowed_fields
            if field in snapshots.catalog.fields
        }
        occupancy: dict[str, int] = {dataset: 0 for dataset in viable}
        for item in snapshots.inventory.records:
            if item.queue_status == "PENDING_SIMULATION" and item.dataset in occupancy:
                occupancy[item.dataset] += 1
        return occupancy

    @staticmethod
    def _research_dataset_priority(
        snapshots: LocalSnapshots,
        allowed_fields: set[str],
    ) -> tuple[str, ...]:
        """Order viable datasets by active pending occupancy, then deterministically."""

        occupancy = HighQualityGenerator._dataset_occupancy(snapshots, allowed_fields)
        return tuple(sorted(occupancy, key=lambda dataset: (occupancy[dataset], dataset)))

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
        # Only a genuine mix is a cross-dataset fault.  An empty set means no field
        # resolved at all, which PLAN_UNKNOWN_FIELD above already reports: any plan
        # reaching here with zero datasets has fields that are empty or outside
        # allowed_fields, so nothing is lost by not double-reporting it. Using != 1
        # made unresolvable-field plans masquerade as cross-dataset ones and
        # inflated the count that diagnosis reads.
        if len(datasets) > 1:
            issues.append("PLAN_CROSS_DATASET")
        # The gate refuses *crowding*: a dataset that already carries more pending
        # candidates than the least-loaded one.  Comparing against priority[0]
        # instead made it mandate one specific dataset, and since priority is
        # sorted (occupancy, name), a fresh queue puts every dataset at occupancy
        # 0 and priority[0] degrades to "alphabetically first" -- 1 acceptable
        # dataset out of the real catalog's 297, for no research reason.  Being
        # outside _LOCALLY_GROUNDABLE_PLAN_ISSUES, that aborted whole cycles.
        occupancy = HighQualityGenerator._dataset_occupancy(snapshots, allowed_fields)
        if len(occupancy) >= 3 and datasets and occupancy:
            floor = min(occupancy.values())
            chosen = next(iter(datasets))
            if occupancy.get(str(chosen), floor) > floor:
                issues.append("PLAN_DATASET_CONCENTRATION")
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
        # An event stream reaching any non-reducer is refused by the platform, not
        # locally, so nothing upstream of here catches it.  Checked with the other
        # field-semantics rules, before the plan whitelist, because it is a
        # property of the field itself rather than of this plan.
        if _unreduced_vector_fields(expression, snapshots.catalog):
            return "VECTOR_FIELD_NOT_REDUCED"
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
        if known_feedback_refs and not feedback_refs <= known_feedback_refs:
            return "HALLUCINATED_FEEDBACK_REF"
        parents = {str(getattr(seed, "expression", "")) for seed in seeds}
        parent = str(row.get("parent_seed") or "")
        if parent not in parents:
            return "UNKNOWN_PARENT_SEED"
        validator = LocalExpressionValidator(snapshots.catalog, allow_stale_catalog=True)
        group_axes = _group_axis_identifiers(expression)
        issues = [
            issue
            for issue in validator.validate(expression, expected_dataset_id=next(iter(datasets)))
            if not _suppressible_scope_issue(issue, group_axes)
        ]
        if issues:
            return "LOCAL_VALIDATION_" + issues[0].code
        history = [
            item.expression for item in snapshots.feedback.records if item.grounded and item.expression
        ]
        inventory = list(snapshots.inventory.expressions)
        existing = history + inventory
        # 所有候选（含降级）必须通过精确去重检查
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

        # 所有候选（含降级）使用统一的历史相似度检查
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
        row = _complete_mechanism_roles(
            row, fields, functions, tolerated_operators=_symbol_operator_names(expression),
        )
        narrative_issue = _narrative_expression_issue(row, plan, fields, functions)
        if narrative_issue:
            return narrative_issue
        mechanism_issue = _mechanism_issue(row, expression, fields, functions, snapshots)
        if mechanism_issue:
            return mechanism_issue
        if _has_short_window(expression):
            return "SHORT_WINDOW"
        if _bare_price_expression(fields):
            return "BARE_PRICE_RISK"
        # Shape policy runs after the evidence gates so a row that both misdeclares
        # its roles and is degenerate reports the misdeclaration, which is the more
        # specific defect.
        if _degenerate_shape(expression, fields):
            return "DEGENERATE_SHAPE"
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
        # 所有候选（含降级）使用统一的质量阈值，不降低标准
        effective_threshold = self._quality_threshold(snapshots)
        if score < effective_threshold:
            return "LOW_LOCAL_QUALITY"
        try:
            settings = _settings(row.get("settings"), snapshots.catalog.info)
            if self.settings_contract is not None:
                # prepare() returns the wire object, which must not carry
                # alpha_type -- the endpoint refuses it inside "settings".  The
                # stored candidate settings are a business record and keep it,
                # both for the queue row and for the gateway's outer "type".
                settings = {
                    "alpha_type": self.settings_contract.alpha_type(settings),
                    **self.settings_contract.prepare(settings),
                }
        except ValueError:
            return "INVALID_SIMULATION_SETTINGS"
        generator_source = str(
            row.get("generator_source")
            or ("LLM_LOCALLY_GROUNDED_PLAN" if plan.get("_locally_grounded") else "LLM_REFINED_V50")
        )
        degraded = generator_source == "DETERMINISTIC_LOCAL_FALLBACK"
        evidence["degraded"] = degraded
        research_direction = str(
            (row.get("research_direction") if degraded else plan.get("research_direction")) or ""
        )
        hypothesis = str((row.get("hypothesis") if degraded else plan.get("hypothesis")) or "")
        return AcceptedCandidate(
            expression=expression,
            settings=settings,
            datasets=tuple(sorted(datasets)),
            parent_seed=parent,
            research_direction=research_direction,
            hypothesis=hypothesis,
            economic_rationale=rationale,
            knowledge_refs=tuple(sorted(refs)),
            feedback_refs=tuple(sorted(feedback_refs)),
            anti_corr_design=anti,
            expected_turnover_behavior=str(plan.get("expected_turnover_behavior") or ""),
            local_quality_score=score,
            novelty_score=max(0.0, 1.0 - history_risk),
            self_corr_risk_score=self_risk,
            quality_evidence=evidence,
            generator_source=generator_source,
            context_refs=tuple(item.ref_id for item in knowledge.snippets),
            knowledge_context_hash=str(knowledge.context_hash or ""),
            degraded=degraded,
        )

    def _quality_threshold(self, snapshots: LocalSnapshots) -> float:
        base = (
            self.offline_quality_threshold
            if snapshots.catalog.info.get("source") == "local_offline_field_snapshot"
            else self.quality_threshold
        )
        return base * self._cold_start_threshold_ratio(snapshots)

    @staticmethod
    def _cold_start_threshold_ratio(snapshots: LocalSnapshots) -> float:
        """Scale the gate to the points a candidate can actually earn.

        The 20-point grounded-feedback tier requires a platform-verified
        PASS/NEAR_PASS record. While the platform is unreachable no candidate can
        earn it, so it is absent evidence - identical for every candidate - not a
        quality difference between them. Judging out of 100 in that state caps the
        best reachable candidate at 80 against a 75 gate, and the resulting
        5.00-point margin is consumed by 26*similarity at 0.192, even though the
        similarity gate itself only rejects at 0.65/0.72. Candidates in that dead
        band were refused as LOW_LOCAL_QUALITY for evidence the platform outage
        withheld.

        Scaling the threshold rather than the score keeps `local_quality_score`
        meaning the same thing in every cycle - an ungraded candidate still cannot
        reach the >85 band reserved for platform-verified evidence - while every
        other requirement stays exactly as strict.
        """

        if snapshots.feedback.positive or snapshots.feedback.near_pass:
            return 1.0
        return (100.0 - 20.0) / 100.0

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
        # Structural depth is reported for ranking transparency but is not summed:
        # a degenerate shape is refused by an explicit gate, not by arithmetic.
        structure_component = _structural_depth_component(expression, fields)
        value = (
            field_component + feedback_component + novelty_component
            + mechanism_component + knowledge_component + risk_component
        )
        # The cap is a claim about evidence, not a ranking knob: only a candidate
        # citing platform-verified feedback may score above 85. Cold-start
        # normalization therefore adjusts the threshold it is compared against
        # (see _quality_threshold), never this value - inflating the score into the
        # graded band would make an ungraded candidate indistinguishable from a
        # verified one in the queue and in every audit that reads it.
        evidence_cap = 100.0 if positive_support or near_support else 85.0
        value = min(value, evidence_cap)
        feedback_attainable = bool(snapshots.feedback.positive or snapshots.feedback.near_pass)
        cold_start = not feedback_attainable
        evidence = {
            "local_quality_score_definition": "local candidate ranking only; not platform Sharpe or Fitness",
            "quality_stage": "LOCAL_UNVERIFIED",
            "platform_verified": False,
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
            # Recorded so an audit can tell a cold-start score from a graded one.
            "cold_start_normalized": cold_start,
            "graded_feedback_available": feedback_attainable,
            "score_components": {
                "field_quality": round(field_component, 2),
                "grounded_feedback": round(feedback_component, 2),
                "novelty_low_similarity": round(novelty_component, 2),
                "mechanism_role_consistency": round(mechanism_component, 2),
                "knowledge_relevance": round(knowledge_component, 2),
                "structural_depth": round(structure_component, 2),
                "turnover_complexity_concentration_risk": round(risk_component, 2),
            },
            "evidence_cap": evidence_cap,
        }
        return round(min(100.0, value), 2), evidence

    @staticmethod
    def _research_prompt(snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext, cycle_id: str) -> str:
        catalog = snapshots.catalog
        allowed_field_ids = HighQualityGenerator._research_field_ids(snapshots, seeds)
        dataset_priority = HighQualityGenerator._research_dataset_priority(snapshots, allowed_field_ids)
        # Name only operators this catalog actually exposes.  Naming a family the
        # catalog lacks makes the plan unsatisfiable: the offline snapshot in use
        # carries no grouping operator at all, so demanding one produced a plan
        # that failed its own scope check.
        _usable = set(catalog.operators) - _GHOST_OPERATORS
        _change_ops = sorted(_usable & {"ts_delta", "ts_std_dev", "ts_corr", "ts_returns", "subtract"})
        _normalizer_ops = sorted(_usable & {"ts_zscore", "ts_rank", "rank", "ts_decay_linear", "divide"})
        _group_ops = sorted(item for item in _usable if item.startswith("group_"))
        # Group the visible fields by dataset instead of listing them flat.  The
        # single-dataset plan rule was only ever satisfied by accident: while a
        # dictionary-order slice made one dataset 91.4% of the view, "pick one
        # dataset" needed no work.  Once every dataset is evenly reachable the
        # model has to choose, and a flat array hides the thing it must choose
        # between - dataset is buried as a per-record attribute across 100+
        # entries.  Keying by dataset makes the unit of choice the structure of
        # the payload rather than a sentence in plan_requirements.
        _fields_by_dataset: dict[str, list[dict[str, str]]] = {}
        for field in sorted(allowed_field_ids):
            entry = catalog.fields[field]
            # field_type is disclosed because it is load-bearing, not decorative:
            # a VECTOR field is an event stream that every operator except a
            # vec_* reducer refuses.  Enforcing that rule while hiding the
            # property would reject candidates for something unobservable.
            _fields_by_dataset.setdefault(str(entry.dataset_id), []).append(
                {
                    "id": entry.field_id,
                    "description": entry.description[:160],
                    "field_type": str(getattr(entry, "field_type", "") or "MATRIX").upper(),
                }
            )
        payload = {
            "cycle_id": cycle_id,
            "catalog": {
                # Derived from the grouping so the dataset list cannot advertise a
                # dataset that has no selectable field in this payload.
                "datasets": sorted(_fields_by_dataset),
                "dataset_priority": list(dataset_priority),
                "fields_by_dataset": _fields_by_dataset,
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
                "Use only field IDs from catalog.fields_by_dataset, catalog.operators names, and knowledge ref IDs shown above.",
                "For arithmetic in an expression use + - * /; if listing operator names, copy the exact spelling from catalog.operators.",
                "catalog.fields_by_dataset is keyed by dataset. First pick exactly ONE of those keys, "
                "then draw every field in fields_to_use from that one key's list. A fields_to_use array "
                "spanning two keys is refused as PLAN_CROSS_DATASET, and the whole plan is discarded.",
                "catalog.dataset_priority is ordered by active pending occupancy, least-loaded "
                "first. Prefer its first entry. A dataset carrying more pending candidates than "
                "the least-loaded one is refused as PLAN_DATASET_CONCENTRATION.",
                "Parent seeds are structural inspiration; never reuse a catalog.forbidden_identifiers token.",
                "Avoid windows below 21, and use at least 42 for ts_corr.",
                "A field whose field_type is VECTOR is an event stream, not one value per day. "
                "Every operator except a vec_* reducer refuses it. So if any field in "
                "fields_to_use has field_type VECTOR, operators_to_use MUST include a vec_* "
                "reducer (vec_avg, vec_sum, vec_count, vec_max, vec_min, vec_range, vec_stddev), "
                "and the expression must wrap that field directly in it -- vec_avg(field), never "
                "ts_std_dev(field, 126). Fields with field_type MATRIX need no wrapper.",
                "operators_to_use becomes the COMPLETE whitelist for the next step, which cannot "
                "add to it, so include everything the hypothesis needs.",
                *(
                    [f"Change operators available here: {', '.join(_change_ops)}. Include at least one."]
                    if _change_ops else []
                ),
                *(
                    [f"Normalizers available here: {', '.join(_normalizer_ops)}. Include at least one."]
                    if _normalizer_ops else []
                ),
                *(
                    [f"Grouping operators available here: {', '.join(_group_ops)}. Include one so a "
                     "peer-relative shape stays reachable."]
                    if _group_ops
                    else ["This catalog exposes no grouping operator, so a peer-relative shape is not "
                          "reachable. Express the relationship between two allowed fields instead."]
                ),
                "A single field wrapped in a single non-grouping operator is refused downstream as a "
                "degenerate shape, so plan for at least two fields or a change-plus-normalizer pair.",
                "operator_roles in the next step must be derived from the expression that step writes, "
                "not copied from this whitelist: naming an operator the expression does not use is "
                "refused as a false claim.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _candidate_prompt(snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext, plan: dict[str, Any]) -> str:
        allowed_fields = sorted(_string_set(plan.get("fields_to_use")))
        # 字段-数据集映射：帮助LLM避免跨数据集混用
        field_dataset_map = {
            f: snapshots.catalog.fields[f].dataset_id
            for f in allowed_fields
            if f in snapshots.catalog.fields
        }
        _allowed_operators = sorted(_string_set(plan.get("operators_to_use")))
        _has_group_operator = any(item.startswith("group_") for item in _allowed_operators)
        # The vec_* gate runs on the expression THIS step writes, so field_type has
        # to be disclosed here too -- field_dataset_map carries the dataset but not
        # the type, which is the same defect the window rule had.
        _vector_fields = [
            field
            for field in allowed_fields
            if str(
                getattr(snapshots.catalog.fields.get(field), "field_type", "") or ""
            ).upper() == "VECTOR"
        ]
        _vector_reducers = sorted(
            item for item in _allowed_operators if item.startswith(_VECTOR_REDUCER_PREFIX)
        )
        payload = {
            "plan": plan,
            "allowed_fields": allowed_fields,
            "field_dataset_map": field_dataset_map,
            "vector_fields_requiring_reduction": _vector_fields,
            "allowed_operators": _allowed_operators,
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
                # The window gate runs on the expression THIS step writes, but the
                # rule was only stated in the plan prompt, so the model that
                # actually chooses the numbers never saw it.
                "Every ts_* window must be at least 21, and at least 42 for ts_corr. A smaller "
                "window is rejected as SHORT_WINDOW. This applies to every window in the "
                "expression you write, including inner ones.",
                # Stated where the expression is written, and only in the form the
                # local whitelist can actually satisfy.
                *(
                    [
                        "vector_fields_requiring_reduction lists allowed_fields that are event "
                        "streams (several records per instrument per day). Every operator except "
                        f"a vec_* reducer refuses them. Wrap each one directly in {_vector_reducers[0]}"
                        f"(field) -- available reducers: {', '.join(_vector_reducers)}. The wrapper "
                        "must be immediate: ts_std_dev(vec_avg(f), 126) is correct, "
                        "ts_std_dev(f, 126) and vec_avg(ts_delta(f, 21)) are both rejected as "
                        "VECTOR_FIELD_NOT_REDUCED."
                    ]
                    if _vector_fields and _vector_reducers
                    else []
                ),
                *(
                    [
                        "vector_fields_requiring_reduction lists allowed_fields that are event "
                        "streams, and allowed_operators contains NO vec_* reducer, so they cannot "
                        "be used at all. Build the expression only from the remaining "
                        "allowed_fields."
                    ]
                    if _vector_fields and not _vector_reducers
                    else []
                ),
                # turnover_controls/correlation_diversifiers are matched against the
                # tokens of the expression, so a generic phrase can never satisfy them.
                "turnover_controls and correlation_diversifiers must each name at least one field ID "
                "or operator that appears in your expression - an arithmetic operator you wrote as a "
                "symbol counts. A generic description such as \"slow window\" satisfies neither and "
                "is rejected as TURNOVER_CONTROL_MISMATCH or ANTI_CORR_DESIGN_MISMATCH.",
                "Use only exact allowed IDs and include a specific economic rationale tied to the selected fields.",
                "Every concrete technical claim in research_direction, hypothesis, economic_rationale, anti_corr_design, "
                "and expected_turnover_behavior needs expression evidence. Do not claim price/returns, volume/adv20, "
                "market-cap scaling, a group-neutralization, ts_decay_linear, or a revision leg unless it is actually used.",
                "Every selected field and operator must appear verbatim in the plan's fields_to_use and operators_to_use arrays.",
                "Treat plan.fields_to_use and plan.operators_to_use as complete whitelists; do not use any other token from v50_seeds or candidate_inventory.",
                "Parent seeds are mechanism context only. Never copy cap, volume, adv20, returns, market, sector, or any other parent token unless it is explicitly in the plan whitelist.",
                # Only offer peer-group neutralization when the whitelist can express
                # it.  Offering it unconditionally made this rule recommend the exact
                # operator the critic then rejected as out of scope.
                (
                    "A single allowed field wrapped in a single non-grouping operator is refused by a shape "
                    "gate: it expresses a level, not a relationship. Express a relationship instead - a change "
                    "against its own scale, a ratio between two allowed fields, or a peer-group neutralization."
                    if _has_group_operator
                    else "A single allowed field wrapped in a single operator is refused by a shape gate: it "
                    "expresses a level, not a relationship. allowed_operators contains NO grouping operator, "
                    "so peer-group neutralization is unavailable and naming one is rejected. Express the "
                    "relationship with the operators you do have - a change against its own scale, or a "
                    "ratio or difference between two allowed fields."
                ),
                "When a combination cannot be proven legal, switch to a different pair of allowed fields "
                "or a different allowed operator; never collapse to a bare one-field one-operator shape.",
                "Provide field_roles for every field used and operator_roles for every function used in the expression.",
                "field_roles must contain exactly the expression's extracted fields, and operator_roles exactly its extracted functions: no missing or extra entries.",
                "Use field_roles objects with field_id and role keys, and operator_roles objects with operator and role keys.",
                "turnover_controls and correlation_diversifiers must name only fields/operators actually present in the expression.",
                "Do not use any forbidden_identifiers, even when a parent seed contains one.",
                "Avoid repeating candidate_inventory used research directions, field sets, and operator topologies unless grounded feedback supports a material repair.",
                "Use recent_rejection_counts to change the mechanism rather than making a cosmetic clone.",
                "When allowed_feedback_refs is empty, set feedback_patterns_used to an empty JSON array, never a placeholder string.",
                # allowed_feedback_refs are opaque hashes, so the model can only copy
                # or fabricate them; without this rule it fabricated plausible-looking
                # ones and every candidate died on HALLUCINATED_FEEDBACK_REF.
                "feedback_patterns_used must contain only strings copied verbatim, character for "
                "character, from allowed_feedback_refs. These are opaque IDs - you cannot derive or "
                "guess one. If no listed ref genuinely informed this candidate, return an empty array. "
                "An unlisted or altered ref is rejected as HALLUCINATED_FEEDBACK_REF.",
                "CRITICAL – economic_rationale MUST NOT mention any catalog field name that does not appear in allowed_fields. Write the rationale using only generic economic concepts (e.g. 'earnings surprise', 'analyst revision') without quoting other field IDs.",
                "CRITICAL – allowed_operators is the COMPLETE operator whitelist. Every function call in the expression must be an exact string match to one entry in allowed_operators.",
                "CRITICAL – all fields in the expression must belong to the same dataset. Never mix fields from different datasets in one expression. The dataset for each allowed field is shown in the plan.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _deterministic_fallback_rows(
        plan: dict[str, Any], snapshots: LocalSnapshots, seeds: list[Any], knowledge: KnowledgeContext,
    ) -> list[dict[str, Any]]:
        """Build one of the legal candidates available to explicit degraded mode."""

        fields = [
            field for field in _string_set(plan.get("fields_to_use"))
            if field in snapshots.catalog.fields
        ]
        operators = [
            operator for operator in _string_set(plan.get("operators_to_use"))
            if operator in snapshots.catalog.operators and operator not in _GHOST_OPERATORS
        ]
        refs = sorted({item.ref_id for item in knowledge.snippets} & _string_set(plan.get("knowledge_refs")))
        if not refs:
            refs = sorted(item.ref_id for item in knowledge.snippets[:3])  # 兜底：使用前3个可用ref
        parents = [str(getattr(seed, "expression", "")) for seed in seeds if str(getattr(seed, "expression", ""))]
        if not fields or not operators or not refs or not parents:
            return []
        available = set(operators)
        ranked_fields = sorted(fields, key=lambda item: (-_field_quality_component((item,), snapshots), item))[:5]
        group_label = next((label for label in ("sector", "industry", "market") if label in GROUPS), "")
        shapes = _fallback_shapes(ranked_fields, available, group_label, snapshots)
        rows: list[dict[str, Any]] = []
        for expression, used_fields, group_used in shapes:
            functions = extract_functions(expression)
            smoothing = next(
                (fn for fn in functions if fn in {"ts_mean", "ts_zscore", "ts_rank", "ts_decay_linear"}),
                functions[0] if functions else "",
            )
            # A group label is not an extracted field, so it can never satisfy the
            # anti-correlation gate.  Name a field the expression actually uses.
            diversifier = used_fields[-1] if used_fields else ""
            measured = " and ".join(used_fields)
            neutralized = (
                f" and neutralized within its {group_used} peer group" if group_used else ""
            )
            rows.append({
                "expression": expression,
                "settings": {},
                "research_direction": f"degraded composite signal on {used_fields[0]}",
                "hypothesis": (
                    f"A {', '.join(dict.fromkeys(functions))} composite over {measured}, expressed "
                    f"relative to its own scale{neutralized}, may carry information the market "
                    f"prices slowly."
                ),
                "economic_rationale": (
                    f"Revisions to forward-looking estimates diffuse gradually. Measuring the change in "
                    f"{measured}, scaling it by its own dispersion{neutralized}, isolates the "
                    f"idiosyncratic component of that diffusion rather than a raw level or a group tilt."
                ),
                "novelty_reason": (
                    "A locally grounded composite built only from the plan whitelist after invalid model drafts."
                ),
                "anti_corr_design": (
                    f"Peer-group neutralization via {group_used} removes the common factor."
                    if group_used
                    else "Self-scaling by dispersion removes the raw level exposure."
                ),
                "parent_seed": parents[0],
                "knowledge_refs": refs,
                "feedback_patterns_used": [],
                "likely_failure_modes": ["LOW_SHARPE", "LOW_FITNESS"],
                "field_roles": [
                    {"field_id": item, "role": "economic input measured for change and dispersion"}
                    for item in used_fields
                ],
                "operator_roles": [
                    {"operator": function, "role": "signal transformation"} for function in functions
                ],
                "turnover_controls": [smoothing] if smoothing else [],
                "correlation_diversifiers": [diversifier] if diversifier else [],
                "generator_source": "DETERMINISTIC_LOCAL_FALLBACK",
            })
        return rows


def _fallback_shapes(
    fields: list[str],
    operators: set[str],
    group_label: str,
    snapshots: LocalSnapshots,
) -> list[tuple[str, tuple[str, ...], str]]:
    """Build the strongest composite shapes the plan whitelist can legally support.

    A bare ``op(field,126)`` is a degenerate shape the platform has already
    scored far below its Sharpe gate, so it is emitted last and only when the
    whitelist admits nothing richer.  Each entry carries the fields it uses and
    the group label it neutralizes by, so the caller declares accurate roles.
    """

    def arity(name: str) -> int:
        return int(getattr(snapshots.catalog.operators.get(name), "arity", -1))

    ts_ops = [item for item in ("ts_zscore", "ts_rank", "ts_mean") if item in operators and arity(item) == 2]
    change = "ts_delta" if "ts_delta" in operators and arity("ts_delta") == 2 else ""
    normalizer = next((item for item in ("ts_zscore", "ts_rank") if item in operators and arity(item) == 2), "")
    grouped = bool(group_label) and "group_neutralize" in operators and arity("group_neutralize") == 2
    tiers: list[list[tuple[str, tuple[str, ...], str]]] = [[], [], [], []]
    for index, primary in enumerate(fields[:4]):
        secondary = fields[index + 1] if index + 1 < len(fields) else (fields[0] if index else "")

        def add(tier: int, core: str, used: tuple[str, ...]) -> None:
            if grouped:
                tiers[tier].append((f"group_neutralize({core},{group_label})", used, group_label))
            tiers[tier].append((core, used, ""))

        if change and normalizer and secondary:
            add(0, f"{normalizer}({change}({primary},63)/{secondary},126)", (primary, secondary))
        if ts_ops and secondary:
            operator = ts_ops[0]
            add(1, f"{operator}({primary},126)/{operator}({secondary},126)", (primary, secondary))
        if change and normalizer:
            add(2, f"{normalizer}({change}({primary},63),126)", (primary,))
        if ts_ops:
            add(3, f"{ts_ops[0]}({primary},126)", (primary,))
    return [shape for tier in tiers for shape in tier]


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
    settings_contract: Any | None = None,
) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """Quarantine legacy pending rows that cannot prove the v2 quality contract.

    Old rows are retained verbatim apart from status/error fields.  Consumer
    owned terminal states are never touched, and no row is deleted.
    """

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
        elif settings_contract is not None:
            try:
                settings_contract.prepare(
                    _settings(
                        {
                            key: row.get(key)
                            for key in (
                                "alpha_type", "region", "universe", "delay", "decay",
                                "neutralization", "truncation", "language",
                            )
                        },
                        snapshots.catalog.info,
                    )
                )
            except ValueError:
                row["queue_status"] = "REJECTED_LOCAL_REVALIDATION"
                row["last_error_category"] = "INVALID_SIMULATION_SETTINGS"
                row["last_error"] = "candidate settings do not match the synchronized platform schema"
                changes.append((str(row.get("candidate_id") or ""), row["last_error_category"]))
        updated.append(row)
    return updated, changes


def _empty_context() -> KnowledgeContext:
    return KnowledgeContext((), "NO_SEEDS")


def _string_set(value: object) -> set[str]:
    return {str(item).strip() for item in value} if isinstance(value, (list, tuple, set)) else set()


def _fields_by_dataset_scope(
    snapshots: LocalSnapshots, field_ids: set[str],
) -> dict[str, list[str]]:
    """Group allowed field IDs by dataset so "one dataset" is a visible choice.

    Used by the plan repair prompt, which fires exactly when a flat field list
    let a cross-dataset mix through, so restating that scope flatly would repeat
    the condition that caused the rejection.
    """

    grouped: dict[str, list[str]] = {}
    for field_id in sorted(field_ids):
        field = snapshots.catalog.fields.get(field_id)
        if field is None:
            continue
        grouped.setdefault(str(field.dataset_id), []).append(field_id)
    return grouped


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


# Topology carries the most weight, then operators, then field identity. A field
# swap on an unchanged topology is the cosmetic clone the pipeline explicitly
# refuses ("do not modify only a field, window, sign or scalar"), so it must
# score high even though every field ID differs. Weighting field identity above
# topology inverts that and lets a clone through.
_SIMILARITY_WEIGHTS = {"fields": 0.25, "operators": 0.30, "topology": 0.45}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _similarity(left: str, right: str) -> float:
    """Proxy similarity over structural axes, not a bag of string fragments.

    The previous implementation tokenized ``behavior_signature`` with
    ``r"[a-z_]+|\\d+"``, which mixed three unrelated things into one bag:

    * the topology template's own placeholders - ``ts_delta(field#field,#)``
      contributes the literal token ``field``, present in 60 of 63 recorded
      expressions, so nearly every pair shared it for free;
    * shredded field names - ``anl10_ebifq1_pred_surps_v2_2230`` splits into
      ``anl``/``10``/``_ebifq``/..., making the dataset prefix ``anl10_`` a
      shared token between any two fields of that dataset;
    * operator names and numeric windows.

    Measured on the live catalog that scored candidates on unrelated datasets,
    with different fields and different operator topologies, at 0.20-0.50. The
    quality gate charges 26 points per unit of similarity against a 5.00-point
    margin, so anything above 0.192 became unreachable while the similarity gate
    itself only rejects at 0.65/0.72 - a dead band that killed honest candidates
    and reported them as LOW_LOCAL_QUALITY.

    Comparing exact field IDs, exact function names, and the topology as a whole
    keeps an identical expression at 1.0, so the duplicate gates upstream lose no
    strength, while two genuinely different alphas no longer collide on
    boilerplate.
    """

    left_text, right_text = str(left or ""), str(right or "")
    left_signature, right_signature = behavior_signature(left_text), behavior_signature(right_text)
    if not left_signature or not right_signature:
        return 0.0
    field_score = _jaccard(set(extract_fields(left_text)), set(extract_fields(right_text)))
    operator_score = _jaccard(
        {item.lower() for item in extract_functions(left_text)},
        {item.lower() for item in extract_functions(right_text)},
    )
    left_topology = left_signature.split("::", 1)[-1]
    right_topology = right_signature.split("::", 1)[-1]
    if left_topology == right_topology:
        topology_score = 1.0
    elif left_topology in right_topology or right_topology in left_topology:
        # One mechanism nested inside the other: `rank(group_neutralize(...))`
        # against `group_neutralize(...)` adds a cosmetic outer wrapper without
        # changing what the signal measures, so a binary match scores it 0 and
        # calls the pair unrelated.
        topology_score = 0.7
    else:
        topology_score = 0.0
    return round(
        _SIMILARITY_WEIGHTS["fields"] * field_score
        + _SIMILARITY_WEIGHTS["operators"] * operator_score
        + _SIMILARITY_WEIGHTS["topology"] * topology_score,
        6,
    )


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


_ARITHMETIC_OPERATOR_NAMES = {"/": "divide", "*": "multiply", "+": "add", "-": "subtract"}
# `+`/`-` also appear as sign markers (ts_delta(x,-5)), so require a value-like
# token on the left to read them as binary. `/` and `*` have no unary form.
_BINARY_ARITHMETIC_PATTERNS = (
    (re.compile(r"/"), "divide"),
    (re.compile(r"\*"), "multiply"),
    (re.compile(r"[)\w]\s*\+"), "add"),
    (re.compile(r"[)\w]\s*-"), "subtract"),
)


def _symbol_operator_names(expression: str) -> set[str]:
    """Catalog operators an expression uses through arithmetic symbols.

    ``extract_functions`` matches ``name(`` only, so ``a / b`` yields no function
    at all. The prompt tells the model to write arithmetic as ``+ - * /`` *and* to
    name operators from the catalog, whose 15 entries include divide/subtract/
    add/multiply. Obeying both makes ``operator_roles`` a strict superset of the
    extracted functions, which the role gate read as a false claim while the
    completion guard (``claimed <= expected``) refused to repair it - so an honest
    row died with no route back. These names make a claim *permissible*, never
    mandatory, so a row that omits them stays valid too.
    """

    text = str(expression or "")
    return {name for pattern, name in _BINARY_ARITHMETIC_PATTERNS if pattern.search(text)}


def _bare_price_expression(fields: tuple[str, ...]) -> bool:
    return bool(fields) and all(field.lower() in {"close", "open", "high", "low", "vwap", "price"} for field in fields)


def _narrative_expression_issue(
    row: dict[str, Any],
    plan: dict[str, Any],
    fields: tuple[str, ...],
    functions: set[str],
) -> str:
    """Reject only explicit technical claims that lack an expression witness.

    This bounded vocabulary intentionally avoids inferring whether an abstract
    economics statement is true. It covers the concrete ingredients that the
    generator previously copied from parent seeds without carrying them into the
    resulting expression.
    """

    text = " ".join(
        str(source.get(key) or "")
        for source, keys in (
            (plan, ("research_direction", "hypothesis", "economic_mechanism", "expected_turnover_behavior")),
            (row, ("economic_rationale", "anti_corr_design", "expected_turnover_behavior")),
        )
        for key in keys
    ).casefold()
    field_names = {field.casefold() for field in fields}

    def has_field(*names: str) -> bool:
        return any(
            name in field_names or any(re.search(rf"(?:^|_){re.escape(name)}(?:_|$)", field) for field in field_names)
            for name in names
        )

    has_price = has_field("close", "open", "high", "low", "vwap", "price", "returns")
    has_volume = has_field("volume", "adv20", "adv")
    has_cap = has_field("cap", "market_cap", "marketcap")
    has_revision = any("revision" in field or re.search(r"(?:^|_)rev(?:_|$)", field) for field in field_names)
    if re.search(r"\b(price[ -]?volume|volume confirmation)\b", text) and not (has_price and has_volume):
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\bprice momentum\b", text) and not has_price:
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\breturns? (momentum|signal|leg|component)\b", text) and not has_field("returns"):
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\b(adv20|volume) (liquidity |momentum |signal|leg|component|confirmation)", text) and not has_volume:
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\b(market[ -]?cap(?:italization)? scaling|cap[ -]?scaled)\b", text) and not has_cap:
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\b(group|sector|industry)[ -]?neutraliz", text) and not any(item.startswith("group_") for item in functions):
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if "ts_decay_linear" in text and "ts_decay_linear" not in functions:
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    if re.search(r"\brevision leg\b", text) and not has_revision:
        return "NARRATIVE_EXPRESSION_CONTRADICTION"
    return ""


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
    # Omitted roles are auto-completed upstream, so a missing claim here means the
    # row hides an operator its expression uses, and an unjustified extra claim
    # means it names one the expression never uses.  Both are false evidence.  An
    # extra claim backed by an arithmetic symbol in the expression is neither: the
    # operator really is applied, just not as a `name(` call the parser can see.
    if functions - claimed_operators:
        return "MECHANISM_OPERATOR_MISMATCH"
    if claimed_operators - functions - _symbol_operator_names(expression):
        return "MECHANISM_OPERATOR_MISMATCH"
    # Symbol arithmetic counts as present in the expression here for the same
    # reason it does in the role check above: a row writing `a / b` and naming
    # `divide` as its turnover control is describing what it actually wrote.
    expression_items = set(fields) | functions | _symbol_operator_names(expression)
    if not turnover_controls & expression_items:  # 只要求至少有一个交集，不要求严格子集
        return "TURNOVER_CONTROL_MISMATCH"
    if not diversifiers & expression_items:  # 同上
        return "ANTI_CORR_DESIGN_MISMATCH"
    rationale = str(row.get("economic_rationale") or "")
    # This scan tests all 5697 catalog IDs against free prose to catch a rationale
    # that justifies the alpha with a field it does not use. Twelve catalog IDs are
    # bare English words (close, open, high, low, volume, returns, cap, vwap,
    # dividend, split, sharesout, adjfactor), so an ordinary sentence - "the signal
    # is close to zero", "a dividend policy shift" - convicted an otherwise honest
    # row. A prose word is not a field reference, and the eight of those covered by
    # BASE_VARS are already refused as identifiers by the expression gates, so
    # excluding the bare-word IDs here loses no real detection: any multi-token
    # catalog ID (anl10_..., sector_...) is still matched exactly as before.
    _prose_ambiguous = {field for field in snapshots.catalog.fields if not re.search(r"[_0-9]", field)}
    mentioned_catalog_fields = {
        field for field in snapshots.catalog.fields
        if field not in _prose_ambiguous
        and re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", rationale, flags=re.IGNORECASE)
    }
    if not mentioned_catalog_fields <= set(fields):
        return "MECHANISM_FIELD_MISMATCH"
    return ""


def _complete_mechanism_roles(
    row: dict[str, Any],
    fields: tuple[str, ...],
    functions: set[str],
    *,
    tolerated_operators: set[str] | None = None,
) -> dict[str, Any]:
    """Fill only omitted structural role entries; never remove or rewrite claims.

    ``tolerated_operators`` are extra operator claims that are legitimate even
    though the parser cannot see them - arithmetic written as a symbol. Without
    them in the guard an honest row was refused completion outright: one
    symbol-backed claim made ``claimed <= expected`` false, so genuinely omitted
    entries were never filled and the row failed the role gate with no repair.
    """

    completed = dict(row)
    changed = False
    for key, identity_key, expected, tolerated in (
        ("field_roles", "field_id", set(fields), set()),
        ("operator_roles", "operator", set(functions), set(tolerated_operators or ())),
    ):
        raw = completed.get(key)
        if not isinstance(raw, list):
            continue
        entries = [item for item in raw if isinstance(item, dict)]
        claimed = {str(item.get(identity_key) or "").strip().lower() for item in entries}
        if claimed <= {value.lower() for value in expected | tolerated}:
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


def _nesting_depth(expression: str) -> int:
    depth = maximum = 0
    for char in str(expression or ""):
        if char == "(":
            depth += 1
            maximum = max(maximum, depth)
        elif char == ")":
            depth = max(0, depth - 1)
    return maximum


def _degenerate_shape(expression: str, fields: tuple[str, ...]) -> bool:
    """Refuse a bare ``op(field)`` / ``op(field,window)`` shape outright.

    The platform scored exactly this family at Sharpe 0.27 and -0.29 against a
    1.58 gate, so simulating another member spends budget on a known answer.
    One field wrapped in one non-grouping operator expresses a level, not a
    relationship: there is no second quantity, no change, and no peer context.
    A grouped shape is excluded because neutralization supplies peer context.
    """

    unique_fields = {item for item in fields if item}
    unique_functions = {item.lower() for item in extract_functions(expression)}
    if len(unique_fields) != 1 or len(unique_functions) > 1:
        return False
    return not any(item.startswith("group_") for item in unique_functions)


def _structural_depth_component(expression: str, fields: tuple[str, ...]) -> float:
    """Reward a shape that expresses a relationship rather than a bare level.

    Every other component is blind to structure: they read a boolean role table,
    a boolean reference list and a similarity number.  Measured directly, a bare
    ``ts_rank(field,126)`` and a five-deep grouped composite scored identically,
    so the bar could only ever gate novelty.  The platform scored that bare shape
    at Sharpe 0.27 and -0.29 against a 1.58 limit, so the difference has to enter
    the score somewhere.
    """

    unique = {item.lower() for item in extract_functions(expression)}
    value = 0.0
    if len({field for field in fields if field}) >= 2:
        value += 5.0
    if unique & {"ts_delta", "ts_diff", "delta", "ts_returns", "ts_std_dev", "ts_corr"}:
        value += 3.0
    if unique & {"ts_zscore", "ts_rank", "rank", "ts_scale", "zscore", "quantile", "normalize"}:
        value += 3.0
    if any(item.startswith("group_") for item in unique):
        value += 4.0
    if _nesting_depth(expression) >= 3:
        value += 2.0
    return min(15.0, value)


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
