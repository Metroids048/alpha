from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _candidate(candidate_id: str = "candidate-1", request_hash: str = "request-1") -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "request_hash": request_hash,
        "expression": "rank(fixture_close)",
        "queue_status": "PENDING_SIMULATION",
        "region": "USA",
    }


def test_csv_import_is_idempotent_and_projection_is_one_way(tmp_path: Path) -> None:
    from alpha_mining.storage.csv_queue import CandidateCsvQueue
    from alpha_mining.storage.work_items import CandidateWorkStore, WorkflowStatus

    csv_path = tmp_path / "待提交Alpha列表.csv"
    queue = CandidateCsvQueue(csv_path, tmp_path / "events.csv")
    with queue.writer():
        queue.upsert(_candidate())
        rejected = _candidate("candidate-2", "request-2")
        rejected["queue_status"] = WorkflowStatus.REJECTED_LOCAL_REVALIDATION.value
        queue.upsert(rejected)

    store = CandidateWorkStore(tmp_path / "research.sqlite")
    assert store.import_csv(csv_path) == 2
    assert store.import_csv(csv_path) == 0
    store.transition("candidate-1", WorkflowStatus.SIMULATING.value)
    store.project_csv(csv_path)

    rows = CandidateCsvQueue(csv_path, tmp_path / "events.csv").read()
    assert {row["candidate_id"]: row["queue_status"] for row in rows} == {
        "candidate-1": "SIMULATING",
        "candidate-2": "REJECTED_LOCAL_REVALIDATION",
    }


def test_events_are_append_only_and_deduplication_never_rewinds_state(tmp_path: Path) -> None:
    from alpha_mining.storage.work_items import CandidateWorkStore, WorkflowStatus

    database = tmp_path / "research.sqlite"
    store = CandidateWorkStore(database)
    assert store.upsert_candidate(_candidate()) is True
    store.transition("candidate-1", WorkflowStatus.READY_TO_SUBMIT.value, alpha_id="alpha-1")
    duplicate = _candidate("new-candidate-id", "request-1")
    assert store.upsert_candidate(duplicate) is False
    item = store.get_item("candidate-1")
    assert item is not None
    assert item.state == WorkflowStatus.READY_TO_SUBMIT.value
    assert item.alpha_id == "alpha-1"
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM candidate_work_events").fetchone()[0] == 3


def test_stale_batch_confirmation_is_rejected(tmp_path: Path) -> None:
    from alpha_mining.storage.work_items import CandidateWorkStore, WorkflowStatus

    store = CandidateWorkStore(tmp_path / "research.sqlite")
    store.upsert_candidate(_candidate())
    store.transition("candidate-1", WorkflowStatus.DESCRIPTION_VALIDATED.value, alpha_id="alpha-1")
    batch_id, payload_hash = store.create_batch_intent(["candidate-1"])
    store.transition("candidate-1", WorkflowStatus.AWAITING_BATCH_CONFIRMATION.value)
    with pytest.raises(ValueError, match="candidate collection changed"):
        store.confirm_batch(batch_id, payload_hash)


def test_dual_ledger_preflight_refuses_non_empty_database(tmp_path: Path) -> None:
    from alpha_mining.storage.work_items import CandidateWorkStore, initialize_authoritative_database

    canonical = tmp_path / "nested.sqlite"
    legacy = tmp_path / "legacy.sqlite"
    CandidateWorkStore(legacy).upsert_candidate(_candidate())
    CandidateWorkStore(canonical).upsert_candidate(_candidate("canonical", "canonical-request"))
    with pytest.raises(RuntimeError, match="dual-ledger"):
        initialize_authoritative_database(canonical, legacy)
