from __future__ import annotations

import asyncio
import csv
import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def _passing_context():
    from alpha_mining.submitter.guard import CandidateContext

    return CandidateContext(
        alpha_id="alpha-1",
        expression_id="expr-1",
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
        ],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="PASS",
        metrics={"sharpe": 1.4, "self_correlation": 0.5},
        ledger_status="COMPLETE",
        ledger_synced_at=datetime.now(timezone.utc).isoformat(),
        ledger_sync_id="sync-1",
        candidate_sync_id="sync-1",
    )


@pytest.mark.parametrize(
    "status", ["PENDING", "MISSING", "UNKNOWN", "ERROR", "API ERROR", "RUNNING", ""]
)
def test_guard_blocks_any_incomplete_or_error_check(status: str) -> None:
    from alpha_mining.submitter.guard import SubmissionGuard

    context = _passing_context()
    context = type(context)(
        **{
            **context.__dict__,
            "checks": [
                *context.checks,
                {"name": "NEW_PLATFORM_CHECK", "result": status},
            ],
        }
    )
    assert SubmissionGuard().evaluate(context).allowed is False


def test_guard_blocks_empty_checks() -> None:
    from alpha_mining.submitter.guard import SubmissionGuard

    context = _passing_context()
    context = type(context)(**{**context.__dict__, "checks": []})
    decision = SubmissionGuard().evaluate(context)
    assert decision.allowed is False
    assert "CHECKS_MISSING" in decision.reasons


def _seed_snapshot(
    database: Path, *, age_hours: float = 0.0, version: int = 1
) -> dict[str, int]:
    from alpha_mining.storage.migrations import migrate

    migrate(database)
    seen = (
        (datetime.now(timezone.utc) - timedelta(hours=age_hours))
        .isoformat()
        .replace("+00:00", "Z")
    )
    snapshots = (
        ("LOW_SHARPE|USA|TOP3000|1|REGULAR|*|*", "LOW_SHARPE", 1.2, "MIN"),
        ("SELF_CORRELATION|USA|TOP3000|1|REGULAR|*|*", "SELF_CORRELATION", 0.7, "MAX"),
    )
    with sqlite3.connect(database) as con:
        for key, name, limit, direction in snapshots:
            con.execute(
                """INSERT INTO platform_gate_snapshots
                (snapshot_key,gate_name,limit_value,direction,region,universe_name,delay,alpha_type,
                 theme_id,pyramid_id,first_seen_at,last_seen_at,observation_count,source,raw_payload_hash,version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    name,
                    limit,
                    direction,
                    "USA",
                    "TOP3000",
                    "1",
                    "REGULAR",
                    "*",
                    "*",
                    seen,
                    seen,
                    1,
                    "test",
                    "hash",
                    version,
                ),
            )
    return {key: version for key, *_rest in snapshots}


def test_submit_queue_defaults_to_no_execution(tmp_path: Path) -> None:
    from alpha_mining.submitter.queue import ConsultantSubmitQueue

    db = tmp_path / "queue.sqlite"
    versions = _seed_snapshot(db)
    queue = ConsultantSubmitQueue(db)
    assert queue.enqueue(
        _passing_context(), {"regular": "rank(close)"}, versions
    ).allowed
    client = SimpleNamespace(
        submit=lambda _alpha_id: pytest.fail("default execution called client")
    )
    counts = queue.execute_ready(client)
    assert counts["submitted"] == 0
    assert counts["execution_disabled"] == 1


def test_gate_registry_reimport_does_not_advance_snapshot_version(
    tmp_path: Path,
) -> None:
    from alpha_mining.platform.check_parser import parse_gate_observations
    from alpha_mining.platform.gates import GateRegistry, GateScope
    from alpha_mining.storage.migrations import migrate

    db = tmp_path / "gates.sqlite"
    migrate(db)
    payload = {
        "id": "alpha-1",
        "settings": {"region": "USA", "universe": "TOP3000", "delay": 1},
        "is": {
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS", "limit": 1.2, "value": 1.4}
            ]
        },
    }
    observations = parse_gate_observations(
        payload, observed_at=datetime.now(timezone.utc).isoformat()
    )
    registry = GateRegistry(db)
    assert registry.record_many(observations) == 1
    assert registry.record_many(observations) == 0
    snapshot = registry.resolve(
        GateScope(region="USA", universe="TOP3000", delay=1), "LOW_SHARPE"
    )
    assert snapshot is not None
    assert snapshot.version == 1
    assert snapshot.observation_count == 1


def test_legacy_triage_reads_gates_before_writes_without_locking(
    tmp_path: Path,
) -> None:
    from alpha_mining.legacy.importer import LegacyImporter
    from alpha_mining.legacy.service import triage_database
    from alpha_mining.storage.migrations import migrate

    source = tmp_path / "legacy.csv"
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    checks = {
        "checks": [
            {"name": "LOW_SHARPE", "result": "PASS", "limit": 1.2, "value": 1.4},
            {"name": "LOW_FITNESS", "result": "PASS", "limit": 0.9, "value": 1.1},
        ]
    }
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "utc_iso",
                "alpha_id",
                "expression",
                "sharpe",
                "fitness",
                "region",
                "universe",
                "delay",
                "platform_check_json",
            ],
        )
        writer.writeheader()
        for index, expression in enumerate(("rank(close)", "rank(volume)"), start=1):
            writer.writerow(
                {
                    "utc_iso": observed_at,
                    "alpha_id": f"a{index}",
                    "expression": expression,
                    "sharpe": 1.4,
                    "fitness": 1.1,
                    "region": "USA",
                    "universe": "TOP3000",
                    "delay": 1,
                    "platform_check_json": json.dumps(checks),
                }
            )
    db = tmp_path / "legacy.sqlite"
    migrate(db)
    LegacyImporter(db).import_sources([source])
    summary = triage_database(db, gate_freshness_hours=24)
    assert summary.clusters == 2


def test_submit_queue_rechecks_snapshot_freshness_and_version(tmp_path: Path) -> None:
    from alpha_mining.submitter.queue import ConsultantSubmitQueue

    db = tmp_path / "queue.sqlite"
    versions = _seed_snapshot(db, age_hours=48)
    queue = ConsultantSubmitQueue(db, gate_freshness_hours=24)
    # Enqueue can preserve a prior guard decision, but execution must recheck freshness.
    decision = queue.enqueue(_passing_context(), {"regular": "rank(close)"}, versions)
    assert decision.allowed is False
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE consultant_submit_queue SET status='READY',reasons_json='[]'"
        )
    client = SimpleNamespace(
        submit=lambda _alpha_id: pytest.fail("stale snapshot called client")
    )
    counts = queue.execute_ready(client, execute=True)
    assert counts["blocked"] == 1
    with sqlite3.connect(db) as con:
        reasons = json.loads(
            con.execute("SELECT reasons_json FROM consultant_submit_queue").fetchone()[
                0
            ]
        )
    assert "GATE_SNAPSHOT_STALE_OR_CHANGED" in reasons


def test_submit_queue_submits_same_alpha_only_once(tmp_path: Path) -> None:
    from alpha_mining.submitter.queue import ConsultantSubmitQueue

    db = tmp_path / "queue.sqlite"
    versions = _seed_snapshot(db)
    queue = ConsultantSubmitQueue(db)
    context = _passing_context()
    queue.enqueue(context, {"regular": "rank(close)"}, versions)
    second = type(context)(**{**context.__dict__, "expression_id": "expr-2"})
    queue.enqueue(second, {"regular": "rank(volume)"}, versions)
    calls: list[str] = []
    client = SimpleNamespace(
        submit=lambda alpha_id: calls.append(alpha_id) or {"ok": True}
    )
    counts = queue.execute_ready(client, execute=True)
    assert calls == ["alpha-1"]
    assert counts == {
        "submitted": 1,
        "blocked": 1,
        "failed": 0,
        "execution_disabled": 0,
    }


def test_correlation_risk_is_fail_and_degenerate_history_is_insufficient() -> None:
    from alpha_mining.correlation.service import CorrelationService

    service = CorrelationService(min_overlap=4, internal_limit=0.65)
    candidate = [(f"2026-01-0{i}", float(i)) for i in range(1, 6)]
    inverted = [(date, -value) for date, value in candidate]
    assert service.compare(candidate, inverted).status == "FAIL"
    constant = [(date, 1.0) for date, _ in candidate]
    assert service.compare(candidate, constant).status == "INSUFFICIENT_HISTORY"


def test_behavior_signature_collapses_offsets_windows_and_settings() -> None:
    from alpha_mining.domain.expression_normalization import behavior_signature

    base = "rank(ts_delta(close, 21))"
    signatures = {
        behavior_signature(base),
        behavior_signature(f"({base}) + 4.2"),
        behavior_signature("rank(ts_delta(close, 22))"),
        behavior_signature(base, settings={"decay": 8, "truncation": 0.03}),
    }
    assert len(signatures) == 1


def test_settings_optimizer_enforces_candidate_budget_and_archived_zero_cost() -> None:
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

    optimizer = SettingsOptimizer(
        max_local_trials=4, total_budget=20, per_candidate_budget=2
    )
    base = optimizer.stage1_default("quality")
    trials = optimizer.local_trials(
        base,
        candidate_id="candidate-1",
        quality_score=0.8,
        metric_ratio=0.95,
        candidate_classification="RECHECK",
    )
    assert len(trials) == 2
    assert all(
        trial.purpose == "STABILITY_TURNOVER_ONLY"
        for trial in trials
        if set(trial.parameter_delta) & {"decay", "truncation"}
    )
    assert (
        optimizer.local_trials(
            base,
            candidate_id="archived-1",
            quality_score=1.0,
            metric_ratio=1.0,
            candidate_classification="ARCHIVE",
        )
        == []
    )
    assert optimizer.candidate_budget_status("archived-1") == "UNUSED"


def test_async_batch_deduplicates_identical_simulations(tmp_path: Path) -> None:
    from alpha_mining.simulate.async_batch import (
        claim_simulation_payloads,
        deduplicate_simulation_payloads,
    )

    payload = {"type": "REGULAR", "settings": {"delay": 1}, "regular": "rank(close)"}
    assert deduplicate_simulation_payloads([payload, dict(payload)]) == [payload]
    database = tmp_path / "claims.sqlite"
    assert claim_simulation_payloads(str(database), [payload]) == [payload]
    assert claim_simulation_payloads(str(database), [payload]) == []


def test_platform_client_opens_circuit_on_429_and_does_not_reauthenticate_401(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        def __init__(self, status: int, retry_after: str = "") -> None:
            self.status_code = status
            self.headers = {"Retry-After": retry_after} if retry_after else {}

    class Session:
        def __init__(self, responses: list[Response]) -> None:
            self.responses = responses
            self.calls = 0

        def request(self, *_args, **_kwargs):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    waits: list[float] = []
    client = ReadOnlyPlatformClient(
        min_interval=0,
        max_attempts=3,
        sleeper=waits.append,
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "api.lock",
    )
    client.session = Session([Response(429, "7"), Response(200)])
    assert client.request("GET", "https://example.test/read").status_code == 429
    assert client.session.calls == 1
    assert waits == []

    auth_calls: list[bool] = []
    second = ReadOnlyPlatformClient(
        min_interval=0,
        database=tmp_path / "auth.sqlite",
        lock_path=tmp_path / "auth.lock",
    )
    second.session = Session([Response(401)])
    second.authenticate = lambda *, force=False: auth_calls.append(force)  # type: ignore[method-assign]
    assert second.request("GET", "https://example.test/read").status_code == 401
    assert auth_calls == []


def test_check_polling_reauthenticates_only_once_on_401(monkeypatch) -> None:
    from alpha_mining.simulate import async_batch

    class Response:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def text(self):
            return "unauthorized"

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    auth_calls: list[int] = []

    async def fake_authenticate(*_args, **_kwargs):
        auth_calls.append(1)

    monkeypatch.setattr(async_batch, "_authenticate", fake_authenticate)
    cfg = SimpleNamespace(
        max_poll_seconds_per_alpha=5,
        poll_fallback_sleep=0,
        poll_error_sleep=0,
        timeout=1,
        submit_timeout=1,
        connect_timeout=1,
    )
    body, status = asyncio.run(
        async_batch._poll_progress(
            Session(),
            cfg,
            "https://example.test/progress",
            asyncio.Semaphore(1),
            asyncio.Lock(),
            None,
        )
    )
    assert body == {}
    assert status == "poll_auth_failed:401"
    assert auth_calls == [1]


def test_legacy_queue_probe_has_no_platform_metric_threshold_parameters() -> None:
    from alpha_mining.scheduler.queue_probe import is_submit_eligible

    parameters = inspect.signature(is_submit_eligible).parameters
    assert "queue_min_sharpe" not in parameters
    assert "queue_min_fitness" not in parameters


def test_vnext_config_contains_margins_not_platform_thresholds() -> None:
    config = (Path(__file__).parents[1] / "alpha_mining" / "config.yaml").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "min_sharpe_threshold",
        "min_fitness_threshold",
        "max_turnover_threshold",
    ):
        assert forbidden not in config


def test_notebook_contains_no_literal_credentials() -> None:
    notebook = (Path(__file__).parents[1] / "Alpha.ipynb").read_text(encoding="utf-8")
    assert "@gmail.com" not in notebook.lower()
    assert 'username = \\"your_email_or_username\\"' in notebook
    assert 'password = \\"your_password\\"' in notebook
