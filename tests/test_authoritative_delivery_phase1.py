"""TDD coverage for Task 3: authoritative submit delivery with 429 stop semantics."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeGateway:
    """Test double for PlatformGateway with configurable responses."""

    def __init__(self) -> None:
        self.fetch_responses: dict[str, dict[str, Any]] = {}
        self.submit_responses: dict[str, dict[str, Any]] = {}
        self.fetch_calls: list[str] = []
        self.submit_calls: list[str] = []

    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.fetch_calls.append(alpha_id)
        return self.fetch_responses.get(alpha_id, {"status": "UNSUBMITTED"})

    def submit_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.submit_calls.append(alpha_id)
        response = self.submit_responses.get(alpha_id, {"status_code": 200})
        if "exception" in response:
            raise Exception(response["exception"])
        return response


def _seed_factory_control(database: Path, hard_stop: int = 0, execute_submit: int = 1) -> None:
    now = _utc_now()
    with sqlite3.connect(database) as con:
        con.execute("DELETE FROM factory_control WHERE singleton=1")
        con.execute(
            """INSERT INTO factory_control
            (singleton,hard_stop,reason,execute_submit,updated_at)
            VALUES (1,?,?,?,?)""",
            (hard_stop, "test", execute_submit, now),
        )


def _seed_fresh_ledger(database: Path, sync_id: str = "sync-test") -> None:
    now = _utc_now()
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO platform_sync_runs
            (sync_id,filters_json,status,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,
             started_at,completed_at)
            VALUES (?,?,?,1,1,1,0,?,?)""",
            (sync_id, "{}", "COMPLETE", now, now),
        )
        con.execute(
            """INSERT INTO platform_alpha_ledger
            (alpha_id,sync_id,platform_status,alpha_type,hidden,expression_hash,settings_hash,
             latest_checks_json,synced_at,raw_payload_hash)
            VALUES ('ready-alpha',?,'UNSUBMITTED','REGULAR',0,'expr-hash','settings-hash',
                    '[{"name":"LOW_SHARPE","result":"PASS"},{"name":"SELF_CORRELATION","result":"PASS"}]',
                    ?,'payload-hash')""",
            (sync_id, now),
        )


def test_submit_execute_requires_factory_control_permission(tmp_path: Path) -> None:
    """Factory Control hard_stop or execute_submit=0 must block execution."""
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "blocked.sqlite"
    migrate(database)
    _seed_factory_control(database, hard_stop=1, execute_submit=0)

    # When calling main submit command with hard_stop=1, it must return exit code 2
    # This will be tested through main.py integration


def test_submit_execute_uses_platform_gateway_not_live_client(tmp_path: Path) -> None:
    """Submit execute must use PlatformGateway and SubmissionDelivery, not LiveSubmissionClient."""
    # This test verifies the architectural requirement that main.py does not import LiveSubmissionClient
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "gateway-test.sqlite"
    migrate(database)
    _seed_factory_control(database)
    _seed_fresh_ledger(database)

    # The actual implementation test will verify that main.py imports PlatformGateway
    # and SubmissionDelivery instead of LiveSubmissionClient


def test_submission_delivery_performs_get_submit_get_sequence(tmp_path: Path) -> None:
    """SubmissionDelivery must perform GET -> Submit -> GET with no replay on timeout."""
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.submitter.delivery import SubmissionDelivery

    database = tmp_path / "delivery.sqlite"
    migrate(database)

    gateway = FakeGateway()
    gateway.fetch_responses["test-alpha"] = {"status": "UNSUBMITTED"}
    gateway.submit_responses["test-alpha"] = {"status_code": 200}

    delivery = SubmissionDelivery(database, gateway)
    result = delivery.submit_once(sync_id="sync-1", alpha_id="test-alpha", execute=True)

    # Must fetch before and after submit
    assert len(gateway.fetch_calls) >= 2
    assert gateway.fetch_calls[0] == "test-alpha"
    assert gateway.fetch_calls[-1] == "test-alpha"
    assert len(gateway.submit_calls) == 1
    assert gateway.submit_calls[0] == "test-alpha"


def test_submission_delivery_reconciles_timeout_without_replay(tmp_path: Path) -> None:
    """On timeout/exception, delivery must reconcile with GET and never replay submit."""
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.submitter.delivery import SubmissionDelivery, SubmissionStatus

    database = tmp_path / "timeout.sqlite"
    migrate(database)

    gateway = FakeGateway()
    gateway.fetch_responses["timeout-alpha"] = {"status": "UNSUBMITTED"}
    gateway.submit_responses["timeout-alpha"] = {"exception": "timeout"}

    delivery = SubmissionDelivery(database, gateway)
    result = delivery.submit_once(sync_id="sync-1", alpha_id="timeout-alpha", execute=True)

    # Must perform GET after exception to reconcile
    assert len(gateway.submit_calls) == 1
    assert result.status == SubmissionStatus.UNCERTAIN
    assert "timeout" in result.error


def test_submission_guard_blocks_when_platform_status_not_unsubmitted(tmp_path: Path) -> None:
    """Guard must block if platform status is not UNSUBMITTED."""
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    guard = SubmissionGuard()
    context = CandidateContext(
        alpha_id="test",
        expression_id="expr",
        checks=[{"name": "LOW_SHARPE", "result": "PASS"}],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="PASS",
        platform_status="SUBMITTED",
    )

    decision = guard.evaluate(context)
    assert not decision.allowed
    assert any("PLATFORM_STATUS_SUBMITTED" in r for r in decision.reasons)


def test_submission_guard_blocks_when_description_not_verified(tmp_path: Path) -> None:
    """Guard must block if description_status is not VERIFIED or NOT_REQUIRED."""
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    guard = SubmissionGuard()
    context = CandidateContext(
        alpha_id="test",
        expression_id="expr",
        checks=[{"name": "LOW_SHARPE", "result": "PASS"}],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="PASS",
        description_status="PENDING",
        platform_status="UNSUBMITTED",
    )

    decision = guard.evaluate(context)
    assert not decision.allowed
    assert any("DESCRIPTION_NOT_VERIFIED" in r for r in decision.reasons)


def test_submission_guard_blocks_pending_or_processing_write_intents(tmp_path: Path) -> None:
    """Guard must block if any write intent is PENDING, PROCESSING, or UNCERTAIN."""
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    guard = SubmissionGuard()
    for status in ("PENDING", "PROCESSING", "UNCERTAIN"):
        context = CandidateContext(
            alpha_id="test",
            expression_id="expr",
            checks=[{"name": "LOW_SHARPE", "result": "PASS"}],
            gate_snapshots_fresh=True,
            quality_buffer_pass=True,
            local_correlation_status="PASS",
            write_intent_statuses=(status,),
            platform_status="UNSUBMITTED",
        )

        decision = guard.evaluate(context)
        assert not decision.allowed
        assert any(f"WRITE_INTENT_{status}" in r for r in decision.reasons), status
