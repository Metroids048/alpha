from __future__ import annotations

import sqlite3
from pathlib import Path


class _SequentialSimulationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.active = 0
        self.max_active = 0

    def simulate(self, *, expression: str, settings: dict, alpha_type: str = "REGULAR"):
        from alpha_mining.factory.orchestrator import SimulationResult

        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((expression, dict(settings)))
        self.active -= 1
        return SimulationResult(
            alpha_id=f"alpha-{len(self.calls)}",
            status="COMPLETE",
            metrics={"sharpe": 1.30, "fitness": 1.10},
            checks=[
                {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
                {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
            ],
            raw={"id": "alpha-1"},
        )


def _research_database(tmp_path: Path) -> Path:
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "factory.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO research_topics
            (topic_id,topic_name_cn,topic_name_en,category,data_category,description,source,created_at,active)
            VALUES ('topic-1','盈利','profitability','fundamental','fundamental','test','fixture','2026-01-01',1)"""
        )
        con.execute(
            """INSERT INTO hypotheses
            (hypothesis_id,topic_id,statement_cn,statement_en,mechanism,horizon,created_at,status)
            VALUES ('h1','topic-1','盈利改善','profit improvement','profitability surprise','medium','2026-01-01','active')"""
        )
        con.execute(
            """INSERT INTO data_mappings
            (mapping_id,hypothesis_id,data_field,dataset_id,rationale,field_quality_score,selected_by,created_at)
            VALUES ('m1','h1','revenue','fundamental6','verified field',1.0,'fixture','2026-01-01')"""
        )
        con.execute(
            """INSERT INTO platform_gate_snapshots
            (snapshot_key,gate_name,limit_value,direction,region,universe_name,delay,alpha_type,
             theme_id,pyramid_id,first_seen_at,last_seen_at,observation_count,source,raw_payload_hash,version)
            VALUES ('low','LOW_SHARPE',1.25,'MIN','USA','TOP3000','1','REGULAR','*','*',
                    '2026-01-01','2999-01-01',1,'fixture','hash',1)"""
        )
        con.execute(
            "UPDATE factory_control SET hard_stop=0,reason='',ledger_sync_id='sync-1',cluster_freeze_complete=1"
        )
    return database


def test_factory_orchestrator_uses_group_rank_free_consultant_candidate(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _research_database(tmp_path)
    simulation = _SequentialSimulationService()
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    assert summary.generated == 7
    assert summary.simulated == 7
    assert summary.baseline_pass == 7
    assert simulation.max_active == 1
    assert "revenue" in simulation.calls[0][0]
    assert "group_rank" not in simulation.calls[0][0]
    with sqlite3.connect(database) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM expressions WHERE generation_strategy='consultant_generator'"
        ).fetchone()[0] == 7
        assert con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 7


def test_factory_orchestrator_uses_safe_base_field_fallback_without_research_rows(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "fallback.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    simulation = _SequentialSimulationService()

    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=2)

    assert summary.generated == 2
    assert summary.simulated == 2
    assert all("close" in expression or "volume" in expression for expression, _ in simulation.calls)


def test_authoritative_runtime_has_no_legacy_v50_delegation() -> None:
    source = Path("alpha_mining/factory/runtime.py").read_text(encoding="utf-8")

    assert "auto_alpha_pipeline_rebuilt_v50" not in source
    assert "WorldQuantAlphaPipeline" not in source
    assert "FactoryOrchestrator" in source


def test_runtime_classifies_recoverable_failures_without_stopping_loop() -> None:
    import sqlite3

    import requests

    from alpha_mining.factory.runtime import recovery_exit_code

    assert recovery_exit_code(sqlite3.OperationalError("database is locked")) == 6
    assert recovery_exit_code(PermissionError("authentication refresh exhausted after HTTP 401")) == 4
    assert recovery_exit_code(requests.Timeout("temporary timeout")) == 3
    assert recovery_exit_code(RuntimeError("unexpected worker failure")) == 7


def test_empty_candidate_batch_is_a_recoverable_cycle_failure() -> None:
    from alpha_mining.factory.orchestrator import FactoryCycleSummary
    from alpha_mining.factory.runtime import cycle_exit_code

    empty = FactoryCycleSummary(0, 0, 0, 0, 0, 0)
    completed = FactoryCycleSummary(1, 1, 0, 0, 1, 0)

    assert cycle_exit_code(empty) == 1
    assert cycle_exit_code(completed) == 0


def test_factory_write_access_defaults_off_and_requires_confirmation(tmp_path: Path) -> None:
    import pytest
    from alpha_mining.factory.control import FactoryControl

    control = FactoryControl(tmp_path / "control.sqlite")
    assert not control.status().execute_description_patch
    with pytest.raises(PermissionError):
        control.set_write_access(patch=True, submit=False, confirmation="wrong")
    enabled = control.set_write_access(
        patch=True, submit=False, confirmation="I_UNDERSTAND_PLATFORM_WRITES"
    )
    assert enabled.execute_description_patch
    assert not enabled.execute_submit


def test_new_alpha_pipeline_prepares_description_after_all_checks_pass(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator, SimulationResult

    class Service:
        def simulate(self, *, expression: str, settings: dict, alpha_type: str = "REGULAR"):
            return SimulationResult(
                alpha_id="alpha-description",
                status="COMPLETE",
                metrics={"sharpe": 1.4, "fitness": 1.1},
                checks=[
                    {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                    {"name": "LOW_FITNESS", "result": "PASS", "mandatory": True},
                    {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
                    {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
                    {"name": "DESCRIPTION", "result": "FAIL"},
                ],
                raw={
                    "id": "alpha-description",
                    "type": "REGULAR",
                    "status": "UNSUBMITTED",
                    "descriptionRequired": True,
                    "descriptionValid": False,
                    "requiredDescriptionSchema": {
                        "payloadPath": ["description", "text"],
                        "minLength": 100,
                        "maxLength": 4000,
                        "requiredSections": [
                            "hypothesis",
                            "data_rationale",
                            "signal_construction",
                            "long_short_interpretation",
                            "settings_rationale",
                            "risks_and_limitations",
                        ],
                    },
                    "fieldMetadata": {"revenue": {"description": "reported revenue"}},
                    "operatorDefinitions": {
                        "rank": "cross-sectional rank",
                        "ts_rank": "time-series rank",
                    },
                },
            )

    database = _research_database(tmp_path)
    summary = FactoryOrchestrator(database, Service()).run_simulate(batch_size=1)

    assert summary.descriptions_validated == 1
    with sqlite3.connect(database) as con:
        row = con.execute(
            "SELECT eligibility_status,description_status,patch_attempt_count FROM description_backfill_jobs"
        ).fetchone()
    assert row == ("SUBMIT_READY_EXCEPT_DESCRIPTION", "VALIDATED", 0)
