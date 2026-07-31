from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alpha_mining.factory.orchestrator import FactoryOrchestrator, SimulationResult
from alpha_mining.factory.runtime import cycle_exit_code


def _seed_factory(tmp_path: Path) -> Path:
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "factory.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO research_topics
               (topic_id,topic_name_cn,topic_name_en,category,data_category,description,source,created_at,active)
               VALUES ('t1','test','test','fundamental','fundamental','','fixture','2026-01-01',1)"""
        )
        con.execute(
            """INSERT INTO hypotheses
               (hypothesis_id,topic_id,statement_cn,statement_en,mechanism,horizon,created_at,status)
               VALUES ('h1','t1','test','test','profitability growth','medium','2026-01-01','active')"""
        )
        con.execute(
            """INSERT INTO data_mappings
               (mapping_id,hypothesis_id,data_field,dataset_id,rationale,field_quality_score,selected_by,created_at)
               VALUES ('m1','h1','revenue','fundamental6','fixture',1,'fixture','2026-01-01')"""
        )
    cached_at = datetime.now(timezone.utc).timestamp()
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({"cached_at": cached_at, "dataset_ids": ["fundamental6"]}), encoding="utf-8"
    )
    (tmp_path / ".alpha_datafields_cache.json").write_text(
        json.dumps({"cached_at": cached_at, "rows": [{"id": "revenue", "_ds": "fundamental6"}]}),
        encoding="utf-8",
    )
    (tmp_path / ".alpha_operators_cache.json").write_text(
        json.dumps(
            {
                "cached_at": cached_at,
                "operators": ["rank", "ts_rank", "ts_delta", "ts_zscore", "ts_std_dev", "ts_mean"],
            }
        ),
        encoding="utf-8",
    )
    return database


def _assert_failed_cycle(database: Path, summary) -> None:
    assert summary.simulated == 0
    assert summary.failed == 1
    assert summary.descriptions_validated == 0
    assert cycle_exit_code(summary) != 0
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT status FROM simulation_requests").fetchone()[0] == "FAILED"
        assert con.execute("SELECT status FROM factory_candidate_claims").fetchone()[0] == "FAILED"
        assert con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM description_backfill_jobs").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("status", "alpha_id"),
    [
        ("FAILED", "alpha-failed"),
        ("ERROR", "alpha-error"),
        ("REJECTED", "alpha-rejected"),
        ("COMPLETE", ""),
        ("RUNNING", "alpha-running"),
    ],
)
def test_error_terminal_results_are_failed(
    tmp_path: Path, status: str, alpha_id: str
) -> None:
    class Service:
        def simulate(self, **_kwargs):
            return SimulationResult(alpha_id, status, {}, [], {"status": status})

    database = _seed_factory(tmp_path)
    summary = FactoryOrchestrator(database, Service()).run_simulate(batch_size=1)

    _assert_failed_cycle(database, summary)


def test_simulate_exception_is_failed(tmp_path: Path) -> None:
    class Service:
        def simulate(self, **_kwargs):
            raise RuntimeError("platform rejected password=secret")

    database = _seed_factory(tmp_path)
    summary = FactoryOrchestrator(database, Service()).run_simulate(batch_size=1)

    _assert_failed_cycle(database, summary)
    with sqlite3.connect(database) as con:
        error = con.execute("SELECT last_error FROM simulation_requests").fetchone()[0]
    assert "secret" not in error
    assert "[REDACTED]" in error


def test_polling_timeout_is_failed(tmp_path: Path) -> None:
    class Service:
        def simulate(self, **_kwargs):
            raise TimeoutError("simulation polling timed out")

    database = _seed_factory(tmp_path)
    summary = FactoryOrchestrator(database, Service()).run_simulate(batch_size=1)

    _assert_failed_cycle(database, summary)


def test_valid_complete_result_is_accepted() -> None:
    from alpha_mining.factory.contracts import validate_simulation_result

    validation = validate_simulation_result(
        SimulationResult("alpha-ok", "COMPLETE", {}, [], {})
    )

    assert validation.valid
    assert validation.normalized_status == "COMPLETE"
