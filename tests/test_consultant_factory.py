from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _check_payload(limit: float = 1.25, result: str = "PASS") -> dict:
    return {
        "id": "alpha-1",
        "settings": {"region": "USA", "universe": "TOP3000", "delay": 1},
        "is": {
            "checks": [
                {"name": "LOW_SHARPE", "result": result, "limit": limit, "value": 1.4},
                {
                    "name": "SELF_CORRELATION",
                    "result": "PASS",
                    "limit": 0.7,
                    "value": 0.55,
                },
                {"message": "new platform check", "result": "PASS"},
            ]
        },
    }


def test_behavior_signature_collapses_sign_coefficients_and_settings() -> None:
    from alpha_mining.domain.expression_normalization import behavior_signature

    base = "rank(ts_delta(close, 21)) - 0.5"
    assert behavior_signature(base) == behavior_signature(f"-({base})")
    assert behavior_signature(base) == behavior_signature(f"2.75 * ({base})")
    assert behavior_signature(base, settings={"decay": 0}) == behavior_signature(
        base, settings={"decay": 8, "neutralization": "MARKET"}
    )


def test_gate_parser_preserves_unknown_and_does_not_guess_missing_limit() -> None:
    from alpha_mining.platform.check_parser import parse_gate_observations

    observations = parse_gate_observations(
        _check_payload(), observed_at="2026-07-20T00:00:00Z"
    )
    assert [item.gate_name for item in observations[:2]] == [
        "LOW_SHARPE",
        "SELF_CORRELATION",
    ]
    unknown = observations[2]
    assert unknown.gate_name.startswith("UNKNOWN_CHECK_")
    assert unknown.limit is None


def test_gate_registry_updates_snapshot_and_fails_stale(tmp_path: Path) -> None:
    from alpha_mining.platform.check_parser import parse_gate_observations
    from alpha_mining.platform.gates import GateRegistry, GateScope, StaleGateSnapshot
    from alpha_mining.storage.migrations import migrate

    db = tmp_path / "registry.sqlite"
    migrate(db)
    registry = GateRegistry(db, freshness_hours=24)
    old = parse_gate_observations(
        _check_payload(1.25), observed_at="2026-07-19T00:00:00Z"
    )
    new = parse_gate_observations(
        _check_payload(1.30), observed_at="2026-07-20T00:00:00Z"
    )
    registry.record_many(old)
    registry.record_many(new)
    scope = GateScope(region="USA", universe="TOP3000", delay=1)
    snapshot = registry.resolve(scope, "LOW_SHARPE")
    assert snapshot is not None and snapshot.limit == pytest.approx(1.30)
    assert snapshot.observation_count == 2
    with pytest.raises(StaleGateSnapshot):
        registry.require_fresh(
            scope, "LOW_SHARPE", now=datetime(2026, 7, 22, tzinfo=timezone.utc)
        )


def test_missing_limit_is_observed_but_does_not_replace_snapshot(
    tmp_path: Path,
) -> None:
    from alpha_mining.platform.check_parser import parse_gate_observations
    from alpha_mining.platform.gates import GateRegistry, GateScope
    from alpha_mining.storage.migrations import migrate

    db = tmp_path / "registry.sqlite"
    migrate(db)
    registry = GateRegistry(db)
    registry.record_many(
        parse_gate_observations(
            _check_payload(1.25), observed_at="2026-07-20T00:00:00Z"
        )
    )
    payload = _check_payload()
    payload["is"]["checks"][0].pop("limit")
    registry.record_many(
        parse_gate_observations(payload, observed_at="2026-07-20T01:00:00Z")
    )
    snapshot = registry.resolve(
        GateScope(region="USA", universe="TOP3000", delay=1), "LOW_SHARPE"
    )
    assert snapshot is not None and snapshot.limit == pytest.approx(1.25)
    with sqlite3.connect(db) as con:
        assert (
            con.execute("SELECT COUNT(*) FROM platform_gate_observations").fetchone()[0]
            == 6
        )


def test_migrations_are_idempotent_and_create_consultant_tables(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    db = tmp_path / "factory.sqlite"
    migrate(db)
    migrate(db)
    with sqlite3.connect(db) as con:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        versions = con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert {
        "platform_gate_observations",
        "platform_gate_snapshots",
        "legacy_alphas",
        "alpha_expression_features",
        "alpha_behavior_clusters",
        "alpha_cluster_members",
        "alpha_lineage",
        "settings_trials",
        "legacy_triage_results",
        "alpha_daily_returns",
        "alpha_correlation_results",
        "consultant_submit_queue",
    } <= tables
    assert versions >= 1


def test_legacy_import_is_chunked_deduplicated_and_preserves_lineage(
    tmp_path: Path,
) -> None:
    from alpha_mining.legacy.importer import LegacyImporter
    from alpha_mining.storage.migrations import migrate

    source = tmp_path / "legacy.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["utc_iso", "alpha_id", "expression", "Sharpe", "Fitness"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "utc_iso": "2026-01-01T00:00:00Z",
                    "alpha_id": "a1",
                    "expression": "rank(close)",
                    "Sharpe": 1.4,
                    "Fitness": 1.1,
                },
                {
                    "utc_iso": "2026-01-02T00:00:00Z",
                    "alpha_id": "a2",
                    "expression": " rank(close) ",
                    "Sharpe": 1.3,
                    "Fitness": 1.0,
                },
                {
                    "utc_iso": "2026-01-03T00:00:00Z",
                    "alpha_id": "a3",
                    "expression": "rank(volume)",
                    "Sharpe": 0.5,
                    "Fitness": 0.4,
                },
            ]
        )
    db = tmp_path / "legacy.sqlite"
    migrate(db)
    summary = LegacyImporter(db, chunk_size=1).import_sources([source])
    assert summary.rows_scanned == 3
    assert summary.canonical_records == 2
    assert summary.lineage_records == 3
    assert summary.chunks_committed == 3


def test_deterministic_medoid_and_triage_near_pass() -> None:
    from alpha_mining.legacy.clustering import deterministic_medoid
    from alpha_mining.legacy.triage import classify_legacy

    members = [
        {"legacy_id": "b", "behavior_signature": "x", "structure_signature": "a>b"},
        {"legacy_id": "a", "behavior_signature": "x", "structure_signature": "a>b>c"},
        {"legacy_id": "c", "behavior_signature": "x", "structure_signature": "a"},
    ]
    assert (
        deterministic_medoid(members)["legacy_id"]
        == deterministic_medoid(list(reversed(members)))["legacy_id"]
    )
    decision = classify_legacy(
        {"parse_valid": True, "sharpe": 1.18, "fitness": 0.95, "checks": []},
        limits={"LOW_SHARPE": 1.25, "LOW_FITNESS": 1.0},
        near_pass_ratio=0.90,
    )
    assert decision.classification == "REPAIR"


def test_correlation_uses_absolute_sign_flip_and_insufficient_history() -> None:
    from alpha_mining.correlation.service import CorrelationService

    service = CorrelationService(min_overlap=4)
    candidate = [(f"2026-01-0{i}", float(i)) for i in range(1, 6)]
    inverted = [(d, -v) for d, v in candidate]
    result = service.compare(candidate, inverted)
    assert result.status == "FAIL"
    assert result.absolute_correlation == pytest.approx(1.0)
    assert result.behavior_risk is True
    short = service.compare(candidate[:2], inverted[:2])
    assert short.status == "INSUFFICIENT_HISTORY"


def test_settings_optimizer_is_ofat_and_budgeted() -> None:
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

    base = {
        "neutralization": "SUBINDUSTRY",
        "decay": 0,
        "truncation": 0.08,
        "nanHandling": "ON",
    }
    optimizer = SettingsOptimizer(max_local_trials=3, total_budget=4)
    trials = optimizer.local_trials(base, quality_score=0.8, metric_ratio=0.95)
    assert len(trials) == 3
    for trial in trials:
        changed = [key for key in base if trial.settings.get(key) != base.get(key)]
        assert len(changed) == 1
    optimizer.consume(4)
    assert optimizer.budget_status() == "BUDGET_EXHAUSTED"


@pytest.mark.parametrize("self_status", ["PENDING", "MISSING", "UNKNOWN", "ERROR"])
def test_submission_guard_fails_closed_for_self_correlation(self_status: str) -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    checks = [{"name": "LOW_SHARPE", "result": "PASS"}]
    if self_status != "MISSING":
        checks.append({"name": "SELF_CORRELATION", "result": self_status})
    decision = SubmissionGuard().evaluate(
        CandidateContext(
            alpha_id="a1",
            expression_id="e1",
            checks=checks,
            gate_snapshots_fresh=True,
            quality_buffer_pass=True,
            local_correlation_status="PASS",
        )
    )
    assert decision.allowed is False
    assert "SELF_CORRELATION" in " ".join(decision.reasons)


def test_submission_guard_blocks_unit_warning_and_insufficient_history() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    context = CandidateContext(
        alpha_id="a1",
        expression_id="e1",
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
        ],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="INSUFFICIENT_HISTORY",
        unit_warnings=("UNIT_MISMATCH",),
    )
    decision = SubmissionGuard().evaluate(context)
    assert decision.allowed is False
    assert {"UNIT_WARNING", "LOCAL_CORRELATION_INSUFFICIENT_HISTORY"} <= set(
        decision.reasons
    )


def test_new_cli_groups_are_available_and_execute_defaults_off(tmp_path: Path) -> None:
    from alpha_mining.main import main

    for command in ("gates", "legacy", "correlation", "consultant", "submit"):
        with pytest.raises(SystemExit) as exc:
            main([command, "--help"])
        assert exc.value.code == 0
    assert main(["submit", "execute", "--database", str(tmp_path / "x.sqlite")]) == 2


def test_alpha_mining_has_no_monolith_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "alpha_mining"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if (
            "import auto_alpha_pipeline_rebuilt_v50" in text
            or "from auto_alpha_pipeline_rebuilt_v50" in text
        ):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
