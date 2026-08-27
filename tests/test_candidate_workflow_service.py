from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


class FakeGateway:
    def __init__(self, *, checks=None) -> None:
        self.posts = 0
        self.refreshes = 0
        self.checks = checks or [
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "PASS"},
        ]

    def simulate(self, **_kwargs):
        from alpha_mining.factory.orchestrator import SimulationResult

        self.posts += 1
        return SimulationResult("alpha-1", "COMPLETE", {"sharpe": 1.8, "fitness": 1.2, "turnover": 0.2}, self.checks, {})

    def refresh_alpha_checks(self, alpha_id: str):
        self.refreshes += 1
        return {"alpha_id": alpha_id, "metrics": {"sharpe": 1.8, "fitness": 1.2, "turnover": 0.2}, "checks": self.checks, "raw": {}}


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "candidate-1", "request_hash": "request-1", "expression": "rank(fixture_close)",
        "region": "USA", "universe": "TOP3000", "delay": "1", "decay": "0", "neutralization": "SUBINDUSTRY", "truncation": "0.08",
    }


def _candidate_with_provenance() -> dict[str, object]:
    return {
        **_candidate(),
        "operator_family": "rank",
        "research_direction": "fundamental quality",
        "economic_hypothesis": "slow fixture information diffusion",
        "datasets": json.dumps(["fixture"]),
        "exact_hash": "exact-1",
        "parameter_skeleton": "rank(FIELD)",
        "field_skeleton": "rank(fixture_close)",
        "knowledge_usage_mode": "LIVE_LLM_KNOWLEDGE",
        "knowledge_refs_json": json.dumps(["worldquant:fixture#1"]),
        "context_refs_json": json.dumps(["worldquant:fixture#1", "worldquant:fixture#2"]),
        "knowledge_context_hash": "context-hash-1",
        "degraded": "true",
        "parent_template": "rank(parent_fixture)",
        "parent_candidate_id": "parent-1",
        "repair_action": "DECAY_FINE",
    }


def _write_catalog(root: Path) -> None:
    import time

    context = {"cached_at": time.time(), "region": "USA", "universe": "TOP3000", "delay": 1}
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["fixture"], "records": [{"id": "fixture"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps({**context, "rows": [{"id": "fixture_close", "_ds": "fixture", "type": "MATRIX"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_operators_cache.json").write_text(
        json.dumps({**context, "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}),
        encoding="utf-8",
    )


def _outcome(database: Path) -> tuple[object, ...]:
    with sqlite3.connect(database) as con:
        return con.execute(
            """SELECT expression,outcome,quality_status,knowledge_refs_json,context_refs_json,
                      knowledge_context_hash,degraded,parent_template,parent_candidate_id,repair_action,
                      error_category,error_message
               FROM candidate_outcomes"""
        ).fetchone()


def test_pending_candidate_is_simulated_and_quality_classified(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    gateway = FakeGateway()
    service = CandidateWorkflowService(tmp_path / "research.sqlite", gateway)
    service.store.upsert_candidate(_candidate())

    summary = service.prepare_once()

    assert summary.simulated == 1
    assert gateway.posts == 1
    item = service.store.get_item("candidate-1")
    assert item is not None
    assert item.state == WorkflowStatus.READY_TO_SUBMIT.value
    assert item.alpha_id == "alpha-1"


def test_waiting_checks_refresh_does_not_post_another_simulation(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    gateway = FakeGateway()
    service = CandidateWorkflowService(tmp_path / "research.sqlite", gateway)
    service.store.upsert_candidate(_candidate())
    service.store.transition("candidate-1", WorkflowStatus.WAITING_CHECKS.value, alpha_id="alpha-1")

    service.prepare_once()

    assert gateway.posts == 0
    assert gateway.refreshes == 1
    assert service.store.get_item("candidate-1").state == WorkflowStatus.READY_TO_SUBMIT.value


def test_near_pass_tuning_is_bounded_to_four_children(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    service = CandidateWorkflowService(tmp_path / "research.sqlite", FakeGateway())
    service.store.upsert_candidate(_candidate())
    service.store.transition("candidate-1", WorkflowStatus.NEAR_PASS.value, alpha_id="alpha-1")
    children = [service.retry_item("candidate-1") for _ in range(5)]

    assert sum(child is not None for child in children) == 4
    assert service.store.get_item("candidate-1").tune_child_count == 4


def test_near_pass_is_automatically_tuned_once_per_round(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    gateway = MetricsGateway(
        {"sharpe": 1.4, "fitness": 1.2, "turnover": 0.2},
        checks=[
            {"name": "LOW_SHARPE", "result": "NEAR_PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "PASS"},
        ],
    )
    service = CandidateWorkflowService(tmp_path / "auto-tune.sqlite", gateway)
    service.store.upsert_candidate(_candidate_with_provenance())

    summary = service.prepare_once()

    assert summary.simulated == 1
    parent = service.store.get_item("candidate-1")
    assert parent is not None and parent.state == WorkflowStatus.NEAR_PASS.value
    children = service.list_items(states=[WorkflowStatus.PENDING_SIMULATION.value])
    assert len(children) == 1
    assert children[0].parent_candidate_id == "candidate-1"
    assert children[0].payload.get("tune_stage") == "STABILITY"


def test_batch_preparation_never_performs_platform_write(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    gateway = FakeGateway()
    service = CandidateWorkflowService(tmp_path / "research.sqlite", gateway)
    service.store.upsert_candidate(_candidate())
    service.store.transition("candidate-1", WorkflowStatus.DESCRIPTION_VALIDATED.value, alpha_id="alpha-1")
    batch = service.submit_batch(["candidate-1"])

    assert not batch.ready_for_confirmation
    assert gateway.posts == 0
    assert service.store.get_item("candidate-1").state == WorkflowStatus.AWAITING_BATCH_CONFIRMATION.value


class MetricsGateway(FakeGateway):
    def __init__(self, metrics, *, checks=None) -> None:
        super().__init__(checks=checks)
        self.metrics = metrics

    def simulate(self, **_kwargs):
        from alpha_mining.factory.orchestrator import SimulationResult

        self.posts += 1
        return SimulationResult("alpha-1", "COMPLETE", self.metrics, self.checks, {})


class ErrorGateway(FakeGateway):
    def __init__(self, *, uncertain: bool) -> None:
        super().__init__()
        self.uncertain = uncertain

    def simulate(self, **_kwargs):
        self.posts += 1
        if self.uncertain:
            from alpha_mining.factory.contracts import SimulationOutcomeUnknown

            raise SimulationOutcomeUnknown("external status cannot be confirmed")
        raise RuntimeError("transport failed")


class AuthPausedGateway(FakeGateway):
    """Reject simulation the way an expired platform session does."""

    def __init__(self, *, pauses: int = 1) -> None:
        super().__init__()
        self.pauses = int(pauses)

    def simulate(self, **kwargs):
        if self.posts < self.pauses:
            from alpha_mining.factory.contracts import SimulationAuthenticationPaused

            self.posts += 1
            raise SimulationAuthenticationPaused("simulation POST returned HTTP 401")
        return super().simulate(**kwargs)


def _second_candidate() -> dict[str, object]:
    return {
        **_candidate_with_provenance(),
        "candidate_id": "candidate-2",
        "request_hash": "request-2",
        "expression": "rank(fixture_close_second)",
        "exact_hash": "exact-2",
        "field_skeleton": "rank(fixture_close_second)",
    }


def _outcome_count(database: Path) -> int:
    with sqlite3.connect(database) as con:
        return int(con.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0])


def test_auth_paused_keeps_candidate_pending_without_quality_outcome(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    database = tmp_path / "auth_paused.sqlite"
    gateway = AuthPausedGateway(pauses=99)
    service = CandidateWorkflowService(database, gateway)
    service.store.upsert_candidate(_candidate_with_provenance())
    service.store.upsert_candidate(_second_candidate())

    summary = service.prepare_once()

    first = service.store.get_item("candidate-1")
    second = service.store.get_item("candidate-2")
    assert first is not None and second is not None
    assert first.state == WorkflowStatus.PENDING_SIMULATION.value
    assert first.last_error_category == "AUTH_PAUSED"
    assert second.state == WorkflowStatus.PENDING_SIMULATION.value
    assert summary.simulated == 0
    assert gateway.posts == 1
    assert _outcome_count(database) == 0


def test_auth_paused_candidate_resumes_into_one_authoritative_outcome(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    database = tmp_path / "auth_resume.sqlite"
    gateway = AuthPausedGateway(pauses=1)
    service = CandidateWorkflowService(database, gateway)
    service.store.upsert_candidate(_candidate_with_provenance())

    paused = service.prepare_once()

    assert paused.simulated == 0
    assert _outcome_count(database) == 0
    item = service.store.get_item("candidate-1")
    assert item is not None and item.state == WorkflowStatus.PENDING_SIMULATION.value

    resumed = service.prepare_once()

    assert resumed.simulated == 1
    assert gateway.posts == 2
    item = service.store.get_item("candidate-1")
    assert item is not None
    assert item.state == WorkflowStatus.READY_TO_SUBMIT.value
    assert _outcome_count(database) == 1
    row = _outcome(database)
    assert row[1:3] == ("READY_TO_SUBMIT", "READY_TO_SUBMIT")
    assert row[10:] == ("", "")


def test_active_simulation_persists_authoritative_outcome_and_provenance(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.generation.snapshots import load_feedback_summary

    database = tmp_path / "research.sqlite"
    candidate = _candidate_with_provenance()
    service = CandidateWorkflowService(database, FakeGateway())
    service.store.upsert_candidate(candidate)

    service.prepare_once()

    row = _outcome(database)
    assert row[0] == candidate["expression"]
    assert row[1:3] == ("READY_TO_SUBMIT", "READY_TO_SUBMIT")
    assert json.loads(row[3]) == ["worldquant:fixture#1"]
    assert json.loads(row[4]) == ["worldquant:fixture#1", "worldquant:fixture#2"]
    assert row[5:10] == ("context-hash-1", 1, "rank(parent_fixture)", "parent-1", "DECAY_FINE")
    assert row[10:] == ("", "")

    feedback = load_feedback_summary(database, queue_rows=[{key: str(value) for key, value in candidate.items()}])
    assert len(feedback.records) == 1
    assert feedback.records[0].expression == candidate["expression"]
    assert feedback.records[0].outcome == "READY_TO_SUBMIT"
    assert feedback.records[0].grounded is True


@pytest.mark.parametrize(
    ("metrics", "checks", "expected"),
    [
        ({"sharpe": 1.4, "fitness": 1.2, "turnover": 0.2}, [{"name": "LOW_SHARPE", "result": "FAIL"}, {"name": "SELF_CORRELATION", "result": "PASS"}, {"name": "PROD_CORRELATION", "result": "PASS"}], "FAR_FAIL"),
        ({"sharpe": 0.5, "fitness": 0.4, "turnover": 0.9}, [{"name": "LOW_SHARPE", "result": "FAIL"}, {"name": "LOW_FITNESS", "result": "FAIL"}, {"name": "HIGH_TURNOVER", "result": "FAIL"}, {"name": "SELF_CORRELATION", "result": "PASS"}, {"name": "PROD_CORRELATION", "result": "PASS"}], "FAR_FAIL"),
    ],
)
def test_active_simulation_persists_platform_quality_classification(tmp_path: Path, metrics, checks, expected: str) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService

    database = tmp_path / f"{expected}.sqlite"
    service = CandidateWorkflowService(database, MetricsGateway(metrics, checks=checks))
    service.store.upsert_candidate(_candidate_with_provenance())

    service.prepare_once()

    assert _outcome(database)[1:3] == (expected, expected)


@pytest.mark.parametrize(("uncertain", "expected", "category"), [(True, "UNKNOWN", "SIMULATION_UNCERTAIN"), (False, "FAILED", "SIMULATION_FAILED")])
def test_active_simulation_persists_unknown_and_failed_outcomes(tmp_path: Path, uncertain: bool, expected: str, category: str) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService

    database = tmp_path / f"{expected}.sqlite"
    service = CandidateWorkflowService(database, ErrorGateway(uncertain=uncertain))
    service.store.upsert_candidate(_candidate_with_provenance())

    service.prepare_once()

    row = _outcome(database)
    assert row[1] == expected
    assert row[10] == category
    assert row[11]


def test_waiting_checks_outcome_upgrades_once_to_final_result(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService

    database = tmp_path / "waiting.sqlite"
    gateway = FakeGateway(checks=[{"name": "SELF_CORRELATION", "result": "PASS"}])
    service = CandidateWorkflowService(database, gateway)
    service.store.upsert_candidate(_candidate_with_provenance())

    service.prepare_once()
    assert _outcome(database)[1] == "WAITING_CHECKS"

    gateway.checks = [
        {"name": "SELF_CORRELATION", "result": "PASS"},
        {"name": "PROD_CORRELATION", "result": "PASS"},
    ]
    service.prepare_once()
    assert _outcome(database)[1:3] == ("READY_TO_SUBMIT", "READY_TO_SUBMIT")

    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0] == 1


def test_next_generation_snapshot_reads_active_simulation_feedback_without_csv_dependency(tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.generation.snapshots import load_local_snapshots

    _write_catalog(tmp_path)
    database = tmp_path / "research.sqlite"
    candidate = _candidate_with_provenance()
    service = CandidateWorkflowService(database, FakeGateway())
    service.store.upsert_candidate(candidate)

    service.prepare_once()
    snapshots = load_local_snapshots(
        root=tmp_path,
        database=database,
        queue_path=tmp_path / "missing-queue.csv",
    )

    assert len(snapshots.feedback.positive) == 1
    feedback = snapshots.feedback.positive[0]
    assert feedback.expression == candidate["expression"]
    assert feedback.request_hash
    assert feedback.grounded is True
    assert feedback.ref_id.startswith("feedback:sqlite:")
