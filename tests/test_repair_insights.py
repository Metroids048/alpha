"""Tests for RepairInsights extraction and HypothesisGenerator prompt injection."""

from __future__ import annotations

import sqlite3
from pathlib import Path


from alpha_mining.filter.insights import RepairInsights, load_repair_insights
from alpha_mining.generator.hypothesis import HypothesisGenerator


# ─── load_repair_insights ─────────────────────────────────────────────────────


def test_returns_empty_when_db_absent(tmp_path: Path) -> None:
    insights = load_repair_insights(tmp_path / "nonexistent.sqlite")
    assert insights.avoided_data_concepts == ()
    assert insights.preferred_horizon is None


def test_returns_empty_when_repairs_table_empty(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    insights = load_repair_insights(db_path)
    assert insights.avoided_data_concepts == ()
    assert insights.preferred_horizon is None


def _seed_repair(db_path: Path, category: str, expression: str) -> None:
    """Insert a minimal expression + repair row for testing."""
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    expression_id = str(uuid.uuid4())
    repair_id = str(uuid.uuid4())
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "INSERT OR IGNORE INTO expressions "
            "(expression_id, expression_text, normalized_text, generation_strategy, generation_layer, created_at) "
            "VALUES (?, ?, ?, 'test', 'L6', ?)",
            (expression_id, expression, expression.lower().replace(" ", ""), now),
        )
        con.execute(
            "INSERT OR IGNORE INTO repairs "
            "(repair_id, expression_id, failure_category, failure_detail, repair_strategy, created_at) "
            "VALUES (?, ?, ?, '', 'test', ?)",
            (repair_id, expression_id, category, now),
        )


def test_extracts_field_tokens_from_prod_correlation_expressions(
    tmp_path: Path,
) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    _seed_repair(db_path, "PROD_CORRELATION", "ts_rank(close, 21)")
    _seed_repair(db_path, "PROD_CORRELATION", "rank(volume, 63)")

    insights = load_repair_insights(db_path)
    assert "close" in insights.avoided_data_concepts
    assert "volume" in insights.avoided_data_concepts
    assert insights.preferred_horizon is None


def test_filters_operator_tokens_from_avoided_concepts(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    _seed_repair(db_path, "PROD_CORRELATION", "group_neutralize(rank(adv20), market)")

    insights = load_repair_insights(db_path)
    assert "group_neutralize" not in insights.avoided_data_concepts
    assert "rank" not in insights.avoided_data_concepts
    assert "market" not in insights.avoided_data_concepts
    assert "adv20" in insights.avoided_data_concepts


def test_preferred_horizon_medium_after_three_ladder_failures(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    for _ in range(3):
        _seed_repair(db_path, "IS_LADDER_SHARPE", "ts_rank(close, 5)")

    insights = load_repair_insights(db_path)
    assert insights.preferred_horizon == "medium"


def test_preferred_horizon_long_after_six_ladder_failures(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    for _ in range(6):
        _seed_repair(db_path, "IS_LADDER_SHARPE", "ts_rank(close, 5)")

    insights = load_repair_insights(db_path)
    assert insights.preferred_horizon == "long"


def test_fewer_than_three_ladder_failures_yields_no_preference(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    for _ in range(2):
        _seed_repair(db_path, "IS_LADDER_SHARPE", "ts_rank(close, 5)")

    insights = load_repair_insights(db_path)
    assert insights.preferred_horizon is None


def test_caps_avoided_concepts_at_eight(tmp_path: Path) -> None:
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = tmp_path / "research.sqlite"
    SqliteRunLog(db_path).initialize_schema()
    fields = [
        "fld_a",
        "fld_b",
        "fld_c",
        "fld_d",
        "fld_e",
        "fld_f",
        "fld_g",
        "fld_h",
        "fld_i",
    ]
    for field in fields:
        _seed_repair(db_path, "PROD_CORRELATION", f"ts_rank({field}, 21)")

    insights = load_repair_insights(db_path)
    assert len(insights.avoided_data_concepts) <= 8


def test_tolerates_broken_sqlite_gracefully(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.sqlite"
    db_path.write_bytes(b"not a sqlite database")
    insights = load_repair_insights(db_path)
    assert insights.avoided_data_concepts == ()
    assert insights.preferred_horizon is None


# ─── HypothesisGenerator._prompt ─────────────────────────────────────────────


def test_prompt_unchanged_without_insights() -> None:
    prompt = HypothesisGenerator._prompt("t1", "desc", [])
    assert "Constraint" not in prompt


def test_prompt_appends_avoided_fields_when_present() -> None:
    insights = RepairInsights(
        avoided_data_concepts=("close", "volume"), preferred_horizon=None
    )
    prompt = HypothesisGenerator._prompt("t1", "desc", [], insights=insights)
    assert "close" in prompt
    assert "volume" in prompt
    assert "Constraint" in prompt


def test_prompt_appends_horizon_preference_when_set() -> None:
    insights = RepairInsights(avoided_data_concepts=(), preferred_horizon="medium")
    prompt = HypothesisGenerator._prompt("t1", "desc", [], insights=insights)
    assert "medium" in prompt
    assert "Constraint" in prompt


def test_prompt_skips_constraint_block_when_insights_empty() -> None:
    insights = RepairInsights(avoided_data_concepts=(), preferred_horizon=None)
    prompt = HypothesisGenerator._prompt("t1", "desc", [], insights=insights)
    assert "Constraint" not in prompt


def test_prompt_includes_both_constraints_when_both_set() -> None:
    insights = RepairInsights(
        avoided_data_concepts=("close",), preferred_horizon="long"
    )
    prompt = HypothesisGenerator._prompt("t1", "desc", [], insights=insights)
    assert "close" in prompt
    assert "long" in prompt
    assert prompt.count("Constraint") == 2


# ─── HypothesisGenerator.use_repair_insights flag ────────────────────────────


def test_use_repair_insights_defaults_to_false() -> None:
    from unittest.mock import MagicMock

    gen = HypothesisGenerator(
        "/tmp/nonexistent.sqlite",
        llm=MagicMock(),
        embedder=MagicMock(),
        model_id="test",
    )
    assert gen.use_repair_insights is False


def test_use_repair_insights_can_be_enabled() -> None:
    from unittest.mock import MagicMock

    gen = HypothesisGenerator(
        "/tmp/nonexistent.sqlite",
        llm=MagicMock(),
        embedder=MagicMock(),
        model_id="test",
        use_repair_insights=True,
    )
    assert gen.use_repair_insights is True
