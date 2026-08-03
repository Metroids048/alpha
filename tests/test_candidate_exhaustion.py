from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.factory.orchestrator import FactoryOrchestrator, SimulationResult
from alpha_mining.factory.runtime import cycle_exit_code
from alpha_mining.generator.consultant_generator import ConsultantGenerator


def _seed(tmp_path: Path) -> Path:
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "exhaustion.sqlite"
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
        candidates = ConsultantGenerator().generate(
            hypothesis_id="h1",
            family="fundamental",
            mechanism="profitability growth",
            horizon="medium",
            fields=("revenue",),
        )
        for index, candidate in enumerate(candidates):
            identity = expression_identity(candidate.expression)
            expression_id = f"historical-{index}"
            con.execute(
                """INSERT INTO expressions
                   (expression_id,expression_text,normalized_text,structure_sig,generation_strategy,generation_layer,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (expression_id, candidate.expression, candidate.expression, "fixture", "fixture", "fixture", "2026-01-01"),
            )
            con.execute(
                """INSERT INTO expression_identities
                   (expression_id,exact_hash,parameter_skeleton,field_skeleton,created_at)
                   VALUES (?,?,?,?,?)""",
                (expression_id, identity.exact_hash, identity.parameter_skeleton, identity.field_skeleton, "2026-01-01"),
            )
    _write_catalog(tmp_path, ["revenue"])
    return database


def _write_catalog(tmp_path: Path, fields: list[str]) -> None:
    cached_at = datetime.now(timezone.utc).timestamp()
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({"cached_at": cached_at, "dataset_ids": ["fundamental6"]}), encoding="utf-8"
    )
    (tmp_path / ".alpha_datafields_cache.json").write_text(
        json.dumps(
            {"cached_at": cached_at, "rows": [{"id": field, "_ds": "fundamental6"} for field in fields]}
        ),
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


class _Service:
    def __init__(self) -> None:
        self.calls = 0

    def simulate(self, **_kwargs):
        self.calls += 1
        return SimulationResult(f"alpha-{self.calls}", "COMPLETE", {"sharpe": 1.3}, [], {})


def test_exhausted_candidate_space_returns_dedicated_state(tmp_path: Path) -> None:
    database = _seed(tmp_path)

    summary = FactoryOrchestrator(database, _Service()).run_simulate(batch_size=60)

    assert summary.generation_state == "CANDIDATE_SPACE_EXHAUSTED"
    assert cycle_exit_code(summary) == 9


def test_exhausted_cycle_does_not_call_simulation(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    service = _Service()

    summary = FactoryOrchestrator(database, service).run_simulate(batch_size=60)

    assert summary.generated == summary.simulated == 0
    assert service.calls == 0
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM factory_candidate_claims").fetchone()[0] == 0


def test_exhausted_cycle_uses_long_backoff() -> None:
    from run_pipeline_loop import CycleOutcome, RecoveryCategory, _recovery_delay

    outcome = CycleOutcome(1, 9, RecoveryCategory.CANDIDATE_EXHAUSTED)

    assert _recovery_delay(outcome, consecutive_failures=1, inter_cycle_sleep=120) >= 3600


def test_new_spec_recovers_from_exhaustion(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    first = FactoryOrchestrator(database, _Service()).run_simulate(batch_size=60)
    assert first.generation_state == "CANDIDATE_SPACE_EXHAUSTED"
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO hypotheses
               (hypothesis_id,topic_id,statement_cn,statement_en,mechanism,horizon,created_at,status)
               VALUES ('h2','t1','new','new','volatility risk','long','2026-01-02','active')"""
        )
        con.execute(
            """INSERT INTO data_mappings
               (mapping_id,hypothesis_id,data_field,dataset_id,rationale,field_quality_score,selected_by,created_at)
               VALUES ('m2','h2','cashflow_op','fundamental6','fixture',1,'fixture','2026-01-02')"""
        )
    _write_catalog(tmp_path, ["revenue", "cashflow_op"])
    service = _Service()

    recovered = FactoryOrchestrator(database, service).run_simulate(batch_size=1)

    assert recovered.generation_state == "READY"
    assert recovered.generated == recovered.simulated == 1
    assert service.calls == 1
