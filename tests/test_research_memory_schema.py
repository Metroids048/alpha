from __future__ import annotations

import sqlite3
from pathlib import Path

from alpha_mining.storage.sqlite_store import SqliteRunLog


EXPECTED_COLUMNS = {
    "research_topics": {
        "topic_id",
        "topic_name_cn",
        "topic_name_en",
        "category",
        "data_category",
        "description",
        "source",
        "created_at",
        "active",
    },
    "hypotheses": {
        "hypothesis_id",
        "topic_id",
        "statement_cn",
        "statement_en",
        "mechanism",
        "horizon",
        "embedding",
        "created_at",
        "llm_model",
        "status",
    },
    "data_mappings": {
        "mapping_id",
        "hypothesis_id",
        "data_field",
        "dataset_id",
        "rationale",
        "field_quality_score",
        "selected_by",
        "created_at",
    },
    "expressions": {
        "expression_id",
        "expression_text",
        "normalized_text",
        "structure_sig",
        "hypothesis_id",
        "parent_expression_id",
        "generation_strategy",
        "generation_layer",
        "embedding",
        "created_at",
        "submission_priority_score",
        "novelty_score",
    },
    "mutations": {
        "mutation_id",
        "parent_expression_id",
        "child_expression_id",
        "mutation_axis",
        "mutation_detail",
        "created_at",
    },
    "repairs": {
        "repair_id",
        "expression_id",
        "failure_category",
        "failure_detail",
        "repair_strategy",
        "resulting_expression_id",
        "success",
        "created_at",
    },
    "hypotheses_staging": {
        "staging_id",
        "topic_id",
        "statement_cn",
        "mechanism",
        "source_url",
        "review_status",
        "trial_pass_rate",
        "created_at",
    },
    "topic_stats": {
        "topic_id",
        "total_generated",
        "total_simulated",
        "total_passed_gate",
        "total_submitted",
        "pass_rate",
        "avg_sharpe",
        "avg_fitness",
        "avg_self_corr",
        "sampling_weight",
        "last_updated",
    },
}

SIMULATION_BASE_COLUMNS = {
    "id",
    "utc_iso",
    "alpha_id",
    "expression",
    "status",
    "queue_status",
    "sharpe",
    "fitness",
    "turnover",
    "fail_reason",
}
SIMULATION_EXTENSION_COLUMNS = {
    "expression_id",
    "region",
    "universe",
    "neutralization",
    "decay",
    "delay",
    "correlation_max",
}


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return {
        row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _foreign_keys(
    connection: sqlite3.Connection, table: str
) -> set[tuple[str, str, str]]:
    return {
        (row[3], row[2], row[4])
        for row in connection.execute(f"PRAGMA foreign_key_list({table})")
    }


def test_initialize_schema_creates_chapter_four_contract(tmp_path: Path) -> None:
    database = tmp_path / "research-memory.sqlite3"
    SqliteRunLog(database).initialize_schema()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert set(EXPECTED_COLUMNS) | {"simulation_runs"} <= tables
        for table, expected in EXPECTED_COLUMNS.items():
            assert set(_columns(connection, table)) == expected
        assert set(_columns(connection, "simulation_runs")) == (
            SIMULATION_BASE_COLUMNS | SIMULATION_EXTENSION_COLUMNS
        )


def test_schema_defaults_and_foreign_keys_match_document(tmp_path: Path) -> None:
    database = tmp_path / "research-memory.sqlite3"
    SqliteRunLog(database).initialize_schema()

    with sqlite3.connect(database) as connection:
        assert _columns(connection, "research_topics")["active"]["dflt_value"] == "1"
        assert _columns(connection, "hypotheses")["status"]["dflt_value"] == "'active'"
        assert (
            _columns(connection, "hypotheses_staging")["review_status"]["dflt_value"]
            == "'pending'"
        )
        stats = _columns(connection, "topic_stats")
        assert stats["total_generated"]["dflt_value"] == "0"
        assert stats["sampling_weight"]["dflt_value"] == "1.0"

        assert ("topic_id", "research_topics", "topic_id") in _foreign_keys(
            connection, "hypotheses"
        )
        assert ("hypothesis_id", "hypotheses", "hypothesis_id") in _foreign_keys(
            connection, "data_mappings"
        )
        assert (
            "parent_expression_id",
            "expressions",
            "expression_id",
        ) in _foreign_keys(connection, "expressions")
        assert ("expression_id", "expressions", "expression_id") in _foreign_keys(
            connection, "simulation_runs"
        )
        assert (
            "resulting_expression_id",
            "expressions",
            "expression_id",
        ) in _foreign_keys(connection, "repairs")


def test_schema_initialization_is_idempotent_and_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research-memory.sqlite3"
    log = SqliteRunLog(database)
    log.append_row(
        utc_iso="2026-07-17T00:00:00Z",
        alpha_id="legacy-alpha",
        expression="rank(close)",
        status="ok",
        queue_status="done",
        sharpe=1.2,
        fitness=1.0,
        turnover=0.2,
        fail_reason="",
    )
    log.initialize_schema()
    log.initialize_schema()

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT alpha_id, expression, sharpe FROM simulation_runs"
        ).fetchone()
        assert row == ("legacy-alpha", "rank(close)", 1.2)
        assert set(_columns(connection, "simulation_runs")) == (
            SIMULATION_BASE_COLUMNS | SIMULATION_EXTENSION_COLUMNS
        )


def test_migrates_preexisting_legacy_simulation_table_without_rebuilding_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE simulation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utc_iso TEXT, alpha_id TEXT, expression TEXT, status TEXT,
                queue_status TEXT, sharpe REAL, fitness REAL, turnover REAL, fail_reason TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO simulation_runs (alpha_id, expression) VALUES (?, ?)",
            ("kept", "rank(volume)"),
        )

    SqliteRunLog(database).initialize_schema()

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT alpha_id FROM simulation_runs"
        ).fetchone() == ("kept",)
        assert set(_columns(connection, "simulation_runs")) == (
            SIMULATION_BASE_COLUMNS | SIMULATION_EXTENSION_COLUMNS
        )
