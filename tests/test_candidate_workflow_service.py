from __future__ import annotations

from pathlib import Path


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
