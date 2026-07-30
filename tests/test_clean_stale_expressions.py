from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def test_clean_stale_expressions_uses_an_injected_database(tmp_path: Path, monkeypatch) -> None:
    from alpha_mining.domain.expression_normalization import expression_identity
    from alpha_mining.storage.maintenance import clean_stale_expressions
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    monkeypatch.chdir(tmp_path)
    database = tmp_path / "maintenance-fixture.sqlite"
    SqliteRunLog(database).initialize_schema()

    expressions = {
        "stale": ("rank(ts_delta(revenue,5))", "2020-01-01T00:00:00Z"),
        "recent": ("rank(ts_rank(revenue,21))", "2026-07-30T00:00:00Z"),
        "completed": ("rank(ts_mean(revenue,63))", "2020-01-01T00:00:00Z"),
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO research_topics
               (topic_id,topic_name_cn,topic_name_en,category,data_category,description,source,created_at,active)
               VALUES ('topic','topic','topic','fixture','fixture','fixture','fixture','2026-01-01',1)"""
        )
        connection.execute(
            """INSERT INTO hypotheses
               (hypothesis_id,topic_id,statement_cn,statement_en,mechanism,horizon,created_at,status)
               VALUES ('hypothesis','topic','test','test','test','medium','2026-01-01','active')"""
        )
        connection.execute(
            """INSERT INTO data_mappings
               (mapping_id,hypothesis_id,data_field,dataset_id,rationale,field_quality_score,selected_by,created_at)
               VALUES ('mapping','hypothesis','revenue','fixture','test',1.0,'test','2026-01-01')"""
        )
        for expression_id, (expression, created_at) in expressions.items():
            identity = expression_identity(expression)
            connection.execute(
                """INSERT INTO expressions
                   (expression_id,expression_text,normalized_text,structure_sig,hypothesis_id,
                    generation_strategy,generation_layer,created_at)
                   VALUES (?,?,?,?,?,'fixture','L5',?)""",
                (expression_id, expression, expression, identity.field_skeleton, "hypothesis", created_at),
            )
            connection.execute(
                """INSERT INTO expression_identities
                   (expression_id,exact_hash,parameter_skeleton,field_skeleton,created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    expression_id,
                    identity.exact_hash,
                    identity.parameter_skeleton,
                    identity.field_skeleton,
                    created_at,
                ),
            )
        connection.execute(
            """INSERT INTO simulation_runs
               (utc_iso,alpha_id,expression,status,queue_status,expression_id)
               VALUES ('2026-01-01','alpha-completed',?,'COMPLETE','done','completed')""",
            (expressions["completed"][0],),
        )

    result = clean_stale_expressions(
        database,
        stale_before=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )

    assert result.deleted_expressions == 1
    assert result.deleted_identities == 1
    assert result.retained_expressions == 2
    with sqlite3.connect(database) as connection:
        assert {
            row[0] for row in connection.execute("SELECT expression_id FROM expressions")
        } == {"recent", "completed"}
        assert connection.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0] == 1
    assert not Path("alpha_state.sqlite3").exists()
