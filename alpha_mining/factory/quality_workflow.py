"""The bounded, quality-first generation workflow; no direct platform or submit I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from alpha_mining.generation.service import CandidateProposal
from alpha_mining.quality.decision import QualityStatus, evaluate_quality
from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore


@dataclass(frozen=True)
class QualityGenerationConfig:
    max_initial_candidates: int = 3
    max_repair_parents: int = 2
    max_repairs_per_parent: int = 4
    max_cycle_simulations: int = 12
    max_24h_simulations: int = 24
    max_ready_per_cycle: int = 1
    concurrency: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_initial_candidates", min(3, max(0, int(self.max_initial_candidates))))
        object.__setattr__(self, "max_repair_parents", min(2, max(0, int(self.max_repair_parents))))
        object.__setattr__(self, "max_repairs_per_parent", min(4, max(0, int(self.max_repairs_per_parent))))
        object.__setattr__(self, "max_cycle_simulations", min(12, max(0, int(self.max_cycle_simulations))))
        object.__setattr__(self, "max_24h_simulations", min(24, max(0, int(self.max_24h_simulations))))
        object.__setattr__(self, "max_ready_per_cycle", min(1, max(0, int(self.max_ready_per_cycle))))
        object.__setattr__(self, "concurrency", 1)


@dataclass(frozen=True)
class CandidateExecutionResult:
    request_hash: str
    result: Any | None = None
    error_category: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class QualityCycleSummary:
    generated: int
    simulated: int
    ready: int
    near_pass: int
    far_fail: int
    state: str
    deferred_reason: str = ""


class CandidateExecutor(Protocol):
    def execute_candidate(self, proposal: CandidateProposal, settings: dict[str, Any]) -> CandidateExecutionResult: ...


class QualityAlphaWorkflow:
    def __init__(
        self,
        *,
        generation_service: Any,
        executor: CandidateExecutor,
        feedback_store: Any,
        ready_store: ReadyAlphaCsvStore,
        config: QualityGenerationConfig | None = None,
    ) -> None:
        self.generation_service = generation_service
        self.executor = executor
        self.feedback_store = feedback_store
        self.ready_store = ready_store
        self.config = config or QualityGenerationConfig()

    def run_cycle(self) -> QualityCycleSummary:
        batch = self.generation_service.generate(limit=self.config.max_initial_candidates)
        generated = simulated = ready = near = far = 0
        partial = False
        for proposal in batch.candidates[: self.config.max_initial_candidates]:
            if simulated >= self.config.max_cycle_simulations or ready >= self.config.max_ready_per_cycle:
                break
            generated += 1
            execution = self.executor.execute_candidate(proposal, self._settings(proposal))
            result = execution.result
            if result is None:
                far += 1
                partial = not self._record_failure(proposal, execution) or partial
                continue
            simulated += 1
            decision = evaluate_quality(
                alpha_id=result.alpha_id,
                status=result.status,
                metrics=result.metrics,
                checks=result.checks,
                prod_corr_exception_confirmed=bool((result.raw or {}).get("prodCorrExceptionConfirmed")),
            )
            partial = not self._record_decision(proposal, execution.request_hash, result, decision) or partial
            if decision.status is QualityStatus.READY_TO_SUBMIT:
                ready += int(self.ready_store.upsert({
                    "alpha_id": result.alpha_id, "exact_hash": proposal.exact_hash,
                    "expression": proposal.expression, "quality_status": decision.status.value,
                    "candidate_id": proposal.candidate_id,
                }))
            elif decision.status is QualityStatus.NEAR_PASS:
                near += 1
            elif decision.status is QualityStatus.FAR_FAIL:
                far += 1
        state = "PARTIAL" if partial else ("READY" if ready else "COMPLETE")
        return QualityCycleSummary(generated, simulated, ready, near, far, state, getattr(batch, "deferred_reason", ""))

    @staticmethod
    def _settings(proposal: CandidateProposal) -> dict[str, Any]:
        return {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 0, "neutralization": "SUBINDUSTRY"}

    def _record_failure(self, proposal: CandidateProposal, execution: CandidateExecutionResult) -> bool:
        try:
            self.feedback_store.record(
                execution.request_hash or proposal.exact_hash, "FAILED", candidate_id=proposal.candidate_id,
                strategy_family=proposal.strategy_family, topic_id=proposal.topic_id,
                error_category=execution.error_category or "EXECUTION_FAILED", error_message=execution.error_message,
            )
            return True
        except Exception:
            return False

    def _record_decision(self, proposal: CandidateProposal, request_hash: str, result: Any, decision: Any) -> bool:
        checks = {str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "") for item in result.checks if isinstance(item, dict)}
        try:
            self.feedback_store.record(
                request_hash or proposal.exact_hash, decision.status.value, candidate_id=proposal.candidate_id,
                topic_id=proposal.topic_id, hypothesis_id=proposal.hypothesis_id, research_family=proposal.research_family,
                strategy_family=proposal.strategy_family, mechanism=proposal.mechanism, dataset=proposal.dataset,
                exact_hash=proposal.exact_hash, parameter_skeleton=proposal.parameter_skeleton, field_skeleton=proposal.field_skeleton,
                sharpe=result.metrics.get("sharpe"), fitness=result.metrics.get("fitness"), turnover=result.metrics.get("turnover"),
                checks=result.checks, quality_status=decision.status.value, quality_reasons=list(decision.reasons),
                self_correlation=checks.get("SELF_CORRELATION", ""),
                prod_correlation=checks.get("PROD_CORRELATION", checks.get("PRODUCTION_CORRELATION", "")),
                knowledge_refs=list(proposal.knowledge_refs), repair_action=proposal.repair_origin,
                operator_topology="", region="USA", universe_name="TOP3000", delay="1",
            )
            return True
        except Exception:
            return False
