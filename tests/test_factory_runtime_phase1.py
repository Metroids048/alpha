from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
    cached_at = datetime.now(timezone.utc).timestamp()
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({"cached_at": cached_at, "dataset_ids": ["fundamental6"]}),
        encoding="utf-8",
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


def test_factory_orchestrator_uses_group_rank_free_consultant_candidate(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _research_database(tmp_path)
    simulation = _SequentialSimulationService()
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    # ConsultantGenerator now ships 14 templates (original 7 + 7 extension skeletons).
    # A fresh DB has no expression_identities, so all 14 are claimed and simulated.
    assert summary.generated == 14
    assert summary.simulated == 14
    assert summary.baseline_pass == 14
    assert simulation.max_active == 1
    assert "revenue" in simulation.calls[0][0]
    assert "group_rank" not in simulation.calls[0][0]
    with sqlite3.connect(database) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM expressions WHERE generation_strategy='consultant_generator'"
        ).fetchone()[0] == 14
        assert con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 14


def test_factory_rejects_an_exact_historical_expression_before_simulation(tmp_path: Path) -> None:
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.generator.consultant_generator import ConsultantGenerator

    database = _research_database(tmp_path)
    historical = ConsultantGenerator().generate(
        hypothesis_id="h1",
        family="fundamental",
        mechanism="profitability surprise",
        horizon="medium",
        fields=("revenue",),
    )[0].expression
    identity = expression_identity(historical)
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO expressions
            (expression_id,expression_text,normalized_text,structure_sig,generation_strategy,generation_layer,created_at)
            VALUES ('historical',?,'historical','historical','fixture','fixture','2026-01-01')""",
            (historical,),
        )
        con.execute(
            """INSERT INTO expression_identities
            (expression_id,exact_hash,parameter_skeleton,field_skeleton,created_at)
            VALUES ('historical',?,?,?,'2026-01-01')""",
            (identity.exact_hash, identity.parameter_skeleton, identity.field_skeleton),
        )

    simulation = _SequentialSimulationService()
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    # Only the exact historical expression is blocked; skeleton siblings remain eligible.
    assert summary.generated == summary.simulated == 13
    assert all(historical != expression for expression, _ in simulation.calls)


def test_factory_claim_allows_same_skeleton_with_a_different_field(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    factory = FactoryOrchestrator(_research_database(tmp_path), _SequentialSimulationService())
    settings = {"region": "USA", "universe": "TOP3000", "delay": 1}

    assert factory._claim("rank(ts_delta(revenue, 21))", settings)
    assert not factory._claim("rank(ts_delta(revenue, 21))", settings)
    assert factory._claim("rank(ts_delta(cashflow_op, 63))", settings)


def test_factory_refreshes_operator_cache_only_from_platform_ledger(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _research_database(tmp_path)
    (tmp_path / ".alpha_operators_cache.json").unlink()
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO platform_sync_runs
            (sync_id,filters_json,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,status,error_message,started_at,completed_at)
            VALUES ('sync-operators','{}',1,1,1,0,'COMPLETE','','2026-01-01','2999-01-01')"""
        )
        con.execute(
            """INSERT INTO platform_alpha_observations
            (sync_id,alpha_id,raw_payload_hash,raw_payload_json,synced_at)
            VALUES ('sync-operators','a1','hash',?,?)""",
            (
                json.dumps(
                    {
                        "operatorDefinitions": {
                            "rank": "rank",
                            "ts_rank": "rank",
                            "ts_delta": "delta",
                            "ts_zscore": "zscore",
                            "ts_std_dev": "std",
                            "ts_mean": "mean",
                        }
                    }
                ),
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )

    summary = FactoryOrchestrator(database, _SequentialSimulationService()).run_simulate(batch_size=1)

    assert summary.generated == summary.simulated == 1
    cache = json.loads((tmp_path / ".alpha_operators_cache.json").read_text(encoding="utf-8"))
    assert cache["source"] == "platform_alpha_observations"


def test_factory_orchestrator_defers_without_verified_catalog_mappings(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "fallback.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    simulation = _SequentialSimulationService()

    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=2)

    assert summary.generated == 0
    assert summary.simulated == 0
    assert summary.generation_state == "NO_RESEARCH_SPECS"
    assert summary.deferred_reason == "no active research specifications are available"
    assert simulation.calls == []
    with sqlite3.connect(database) as con:
        assert con.execute(
            "SELECT category FROM factory_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()[0] == "NO_RESEARCH_SPECS"


def test_factory_rejects_a_field_mapped_to_the_wrong_dataset(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _research_database(tmp_path)
    datasets = tmp_path / ".alpha_datasets_cache.json"
    datasets.write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).timestamp(), "dataset_ids": ["fundamental6", "pv1"]}),
        encoding="utf-8",
    )
    with sqlite3.connect(database) as con:
        con.execute("UPDATE data_mappings SET dataset_id='pv1'")

    summary = FactoryOrchestrator(database, _SequentialSimulationService()).run_simulate(batch_size=1)

    assert summary.deferred_reason == "a mapped field-dataset pair is absent from the verified data-field cache"


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


def test_catalog_deferral_has_a_dedicated_recovery_exit_code() -> None:
    from alpha_mining.factory.orchestrator import FactoryCycleSummary
    from alpha_mining.factory.runtime import cycle_exit_code

    empty = FactoryCycleSummary(0, 0, 0, 0, 0, 0)
    catalog_unavailable = FactoryCycleSummary(0, 0, 0, 0, 0, 0, deferred_reason="stale cache")
    completed = FactoryCycleSummary(1, 1, 0, 0, 1, 0)

    assert cycle_exit_code(empty) == 1
    assert cycle_exit_code(catalog_unavailable) == 8
    assert cycle_exit_code(completed) == 0


def test_runtime_main_loads_workspace_env_before_running_cycle(monkeypatch, tmp_path: Path) -> None:
    import alpha_mining.factory.runtime as runtime

    events: list[str] = []

    def fake_load_workspace_env() -> None:
        events.append("env")

    def fake_run_generation_cycle(config) -> object:
        events.append("cycle")
        assert events == ["env", "cycle"]
        return runtime.GenerationCycleSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, "COMPLETE")

    monkeypatch.setattr(runtime, "load_workspace_env", fake_load_workspace_env)
    monkeypatch.setattr(runtime, "run_generation_cycle", fake_run_generation_cycle)

    assert runtime.main(["--once", "--database", str(tmp_path / "runtime.sqlite")]) == 0
    assert events == ["env", "cycle"]


def test_runtime_cycle_keeps_catalog_failure_fail_closed(monkeypatch, tmp_path: Path) -> None:
    import alpha_mining.factory.runtime as runtime
    from alpha_mining.offline.metadata import MetadataCacheError

    source_calls: list[str] = []

    def unavailable_catalog(*_args, **_kwargs):
        raise MetadataCacheError("catalog unavailable in test")

    def candidate_source():
        source_calls.append("candidate_source")
        return [], None

    monkeypatch.setattr(runtime, "_load_catalog", unavailable_catalog)
    summary = runtime.run_generation_cycle(
        runtime.GenerationCycleConfig(
            database=tmp_path / "runtime.sqlite",
            output=tmp_path / "待提交Alpha列表.csv",
            cache_dir=tmp_path,
            auth_state_file=tmp_path / "auth.json",
            lock_path=tmp_path / "lock",
        ),
        candidate_source=candidate_source,
    )

    assert summary.state == "CATALOG_UNAVAILABLE"
    assert source_calls == []


def test_default_candidate_source_passes_runtime_database_to_knowledge_boundary(monkeypatch, tmp_path: Path) -> None:
    import alpha_mining.factory.runtime as runtime

    captured = {}
    monkeypatch.setattr(runtime, "generate_candidates", lambda **kwargs: (captured.update(kwargs) or ([], None)))
    config = runtime.GenerationCycleConfig(
        database=tmp_path / "runtime.sqlite", output=tmp_path / "ready.csv", cache_dir=tmp_path,
        auth_state_file=tmp_path / "auth.json", lock_path=tmp_path / "lock",
    )

    assert runtime._default_candidate_source(config) == ([], None)
    assert captured == {"knowledge_database": config.database}


def test_arm_budget_excludes_exploration_weight_when_higher_weight_exists(monkeypatch, tmp_path: Path) -> None:
    import alpha_mining.factory.runtime as runtime
    from alpha_mining.factory.v50_adapter import FactoryCandidateProposal

    def proposal(candidate_id: str, family: str) -> FactoryCandidateProposal:
        return FactoryCandidateProposal(candidate_id, "rank(close)", "", "", family, family, "m", "d", "m", candidate_id, "p", "f", "d")

    class Tracker:
        def stats(self, arm):
            return type("Stats", (), {"sampling_weight": {"explore": 0.1, "limited": 0.25, "normal": 1.0}[arm.family]})()

    admitted = runtime._apply_arm_budget(
        [proposal("explore", "explore"), proposal("limited-a", "limited"), proposal("limited-b", "limited"), proposal("normal", "normal")],
        Tracker(),
    )

    assert [item.candidate_id for item in admitted] == ["limited-a", "normal"]


def test_arm_budget_uses_one_deterministic_exploration_slot_when_all_low() -> None:
    import alpha_mining.factory.runtime as runtime
    from alpha_mining.factory.v50_adapter import FactoryCandidateProposal

    def proposal(candidate_id: str) -> FactoryCandidateProposal:
        return FactoryCandidateProposal(candidate_id, "rank(close)", "", "", "low", "low", "m", "d", "m", candidate_id, "p", "f", "d")

    class Tracker:
        def stats(self, _arm):
            return type("Stats", (), {"sampling_weight": 0.1})()

    admitted = runtime._apply_arm_budget([proposal("z"), proposal("a")], Tracker())
    assert [item.candidate_id for item in admitted] == ["a"]


def test_catalog_unavailable_loop_waits_and_retries_until_round_limit(monkeypatch, tmp_path: Path) -> None:
    import alpha_mining.factory.runtime as runtime

    summaries = iter(
        [
            runtime.GenerationCycleSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, "CATALOG_UNAVAILABLE", "429"),
            runtime.GenerationCycleSummary(0, 0, 1, 0, 0, 0, 0, 0, 0, "COMPLETE"),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(runtime, "run_generation_cycle", lambda _config: next(summaries))
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    code = runtime.run_generation_loop(
        runtime.GenerationCycleConfig(
            database=tmp_path / "runtime.sqlite",
            output=tmp_path / "待提交Alpha列表.csv",
            cache_dir=tmp_path,
            auth_state_file=tmp_path / "auth.json",
            lock_path=tmp_path / "lock",
        ),
        max_rounds=2,
        interval_seconds=3,
    )

    assert code == 0
    assert sleeps == [3.0]


def test_catalog_error_includes_recovery_action() -> None:
    from alpha_mining.factory.runtime import _catalog_recovery_hint

    assert "catalog-sync" in _catalog_recovery_hint(RuntimeError("operator cache missing"))
    assert "platform probe" in _catalog_recovery_hint(RuntimeError("CircuitOpen: HTTP 429"))


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
                        "ts_delta": "time-series change",
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
