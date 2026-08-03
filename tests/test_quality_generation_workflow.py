from __future__ import annotations

from dataclasses import dataclass

from alpha_mining.factory.orchestrator import SimulationResult
from alpha_mining.generation.service import CandidateGenerationBatch, CandidateProposal


def _proposal() -> CandidateProposal:
    return CandidateProposal(
        candidate_id="candidate-1", topic_id="topic", hypothesis_id="hypothesis", research_family="momentum",
        strategy_family="momentum", mutation_type="new", mechanism="momentum", dataset="ds",
        expression="rank(close)", parent_template="template", generator_source="test", exact_hash="hash-1",
        parameter_skeleton="param", field_skeleton="field", knowledge_refs=("worldquant:test#1",),
    )


class _Generation:
    def generate(self, *, limit: int):
        return CandidateGenerationBatch((_proposal(),), ("topic",), ("momentum",), {}, "READY", "")


class _Executor:
    def __init__(self):
        self.calls = 0

    def execute_candidate(self, proposal, settings):
        self.calls += 1
        from alpha_mining.factory.quality_workflow import CandidateExecutionResult
        return CandidateExecutionResult(
            request_hash="request-1",
            result=SimulationResult(
                alpha_id="alpha-1", status="COMPLETE",
                metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
                checks=[
                    {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                    {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
                    {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
                ], raw={},
            ),
        )


def test_workflow_applies_frozen_caps_and_writes_only_ready_rows(tmp_path) -> None:
    from alpha_mining.factory.quality_workflow import QualityAlphaWorkflow, QualityGenerationConfig
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "workflow.sqlite"
    migrate(database)
    executor = _Executor()
    workflow = QualityAlphaWorkflow(
        generation_service=_Generation(), executor=executor,
        feedback_store=CandidateFeedbackStore(database), ready_store=ReadyAlphaCsvStore(tmp_path / "待提交Alpha列表.csv"),
        config=QualityGenerationConfig(),
    )

    summary = workflow.run_cycle()

    assert summary.generated == summary.simulated == summary.ready == 1
    assert executor.calls == 1
    assert ReadyAlphaCsvStore(tmp_path / "待提交Alpha列表.csv").read_ready()[0]["alpha_id"] == "alpha-1"


def test_workflow_never_exceeds_frozen_config_bounds() -> None:
    from alpha_mining.factory.quality_workflow import QualityGenerationConfig

    config = QualityGenerationConfig(max_initial_candidates=99, max_cycle_simulations=99, concurrency=9)

    assert config.max_initial_candidates == 3
    assert config.max_cycle_simulations == 12
    assert config.concurrency == 1
