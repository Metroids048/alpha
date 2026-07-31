from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.factory.runtime import cycle_exit_code
from alpha_mining.platform.gateway import PlatformGateway


def _database(tmp_path: Path) -> Path:
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "e2e.sqlite"
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


class _Response:
    def __init__(self, status_code: int, payload: dict, location: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, terminal: str) -> None:
        self.terminal = terminal
        self.calls: list[str] = []

    def authenticate(self) -> None:
        self.calls.append("AUTH")

    def request(self, method: str, *_args, **_kwargs):
        self.calls.append(method)
        if method == "POST":
            return _Response(201, {}, "/simulations/progress/1")
        if self.terminal == "ERROR":
            return _Response(200, {"status": "ERROR"})
        return _Response(200, {"status": "COMPLETE", "alpha": "alpha-e2e"})

    def fetch_alpha(self, alpha_id: str):
        self.calls.append("FETCH")
        return {
            "id": alpha_id,
            "status": "UNSUBMITTED",
            "is": {
                "sharpe": 1.4,
                "fitness": 1.1,
                "turnover": 0.2,
                "checks": [{"name": "LOW_SHARPE", "result": "PASS"}],
            },
        }


def _gateway(database: Path, client: _FakeClient) -> PlatformGateway:
    gateway = PlatformGateway(database=database, poll_interval=0.01, sleeper=lambda _seconds: None)
    gateway.client = client
    return gateway


def test_successful_temporary_database_simulation_e2e(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _FakeClient("COMPLETE")

    summary = FactoryOrchestrator(database, _gateway(database, client)).run_simulate(batch_size=1)

    assert summary.generated == summary.simulated == 1
    assert summary.failed == summary.unknown == 0
    assert cycle_exit_code(summary) == 0
    assert client.calls == ["AUTH", "POST", "GET", "FETCH"]
    with sqlite3.connect(database) as con:
        request = con.execute(
            "SELECT status,progress_location,alpha_id FROM simulation_requests"
        ).fetchone()
        claim = con.execute("SELECT status FROM factory_candidate_claims").fetchone()[0]
        runs = con.execute("SELECT alpha_id FROM simulation_runs").fetchall()
    assert request == ("COMPLETE", "/simulations/progress/1", "alpha-e2e")
    assert claim == "SIMULATED"
    assert runs == [("alpha-e2e",)]


def test_error_temporary_database_simulation_e2e(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _FakeClient("ERROR")

    summary = FactoryOrchestrator(database, _gateway(database, client)).run_simulate(batch_size=1)

    assert summary.generated == 1
    assert summary.simulated == 0
    assert summary.failed == 1
    assert cycle_exit_code(summary) != 0
    assert client.calls == ["AUTH", "POST", "GET"]
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT status FROM simulation_requests").fetchone()[0] == "FAILED"
        assert con.execute("SELECT status FROM factory_candidate_claims").fetchone()[0] == "FAILED"
        assert con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 0
