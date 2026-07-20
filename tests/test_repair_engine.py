"""Tests for L6 RepairEngine (repair.py)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alpha_mining.filter.repair import (
    RepairEngine,
    RepairResult,
    persist_repair,
    _REPAIR_STRATEGIES,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog


engine = RepairEngine()

# ─── classify ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("LOW_SHARPE", "LOW_SHARPE"),
        ("LOW_SHARPE,LOW_FITNESS", "LOW_FITNESS"),
        ("HIGH_TURNOVER", "HIGH_TURNOVER"),
        ("SELF_CORRELATION", "SELF_CORRELATION"),
        ("PROD_CORRELATION above cutoff", "PROD_CORRELATION"),
        ("IS ladder Sharpe below cutoff", "IS_LADDER_SHARPE"),
        ("LOW_SHARPE,SELF_CORRELATION", "SELF_CORRELATION"),
        ("INCOMPATIBLE_UNIT", "INCOMPATIBLE_UNIT"),
        ("SPARSE_SIGNAL", "SPARSE_SIGNAL"),
        ("CONCENTRATED_WEIGHT", "CONCENTRATED_WEIGHT"),
        ("unknown junk", "LOW_SHARPE"),
    ],
)
def test_classify_maps_platform_tokens(text: str, expected: str):
    assert engine.classify(text) == expected


def test_classify_all_returns_multiple_categories():
    categories = engine.classify_all("LOW_SHARPE,SELF_CORRELATION,HIGH_TURNOVER")
    assert "SELF_CORRELATION" in categories
    assert "HIGH_TURNOVER" in categories
    assert len(categories) >= 2


def test_classify_all_empty_fallback():
    categories = engine.classify_all("totally unknown")
    assert categories == ["LOW_SHARPE"]


# ─── repair results ──────────────────────────────────────────────────────────


def test_repair_low_sharpe_wraps_expression():
    result = engine.repair("ts_rank(close, 21)", "LOW_SHARPE")
    assert result.failure_category == "LOW_SHARPE"
    assert result.repaired_expression is not None
    assert "group_neutralize" in result.repaired_expression
    assert not result.needs_regen


def test_repair_high_turnover_lengthens_window():
    result = engine.repair("ts_rank(close, 10)", "HIGH_TURNOVER")
    assert result.repaired_expression is not None
    assert "10" not in result.repaired_expression


def test_repair_concentrated_weight_adds_winsorize():
    result = engine.repair("group_rank(x, subindustry)", "CONCENTRATED_WEIGHT")
    assert result.repaired_expression is not None
    assert "winsorize" in result.repaired_expression


def test_repair_self_correlation_signals_regen():
    result = engine.repair("ts_rank(close, 21)", "SELF_CORRELATION")
    assert result.needs_regen
    assert result.repaired_expression is None


def test_repair_incompatible_unit_signals_regen():
    result = engine.repair("bad_expr", "INCOMPATIBLE_UNIT")
    assert result.needs_regen


@pytest.mark.parametrize("category", ["PROD_CORRELATION", "IS_LADDER_SHARPE"])
def test_new_platform_failures_signal_regeneration(category: str):
    result = engine.repair("ts_rank(close, 21)", category)
    assert result.needs_regen
    assert result.repaired_expression is None


def test_repair_all_categories_have_strategy():
    from alpha_mining.filter.repair import _FAILURE_PATTERNS

    categories = {cat for _, cat in _FAILURE_PATTERNS}
    for cat in categories:
        result = engine.repair("ts_rank(x, 21)", cat)
        assert isinstance(result, RepairResult)
        assert result.repair_strategy in _REPAIR_STRATEGIES.values()


# ─── persist_repair ──────────────────────────────────────────────────────────


def test_persist_repair_writes_row(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    db.initialize_schema()
    rid = persist_repair(
        db,
        expression_id="expr-1",
        failure_category="LOW_SHARPE",
        failure_detail="sharpe=0.8",
        repair_strategy="add_cross_sectional_norm",
        resulting_expression_id="expr-2",
        success=True,
    )
    with sqlite3.connect(str(db.path)) as con:
        row = con.execute(
            "SELECT failure_category, success FROM repairs WHERE repair_id=?", (rid,)
        ).fetchone()
    assert row is not None
    assert row[0] == "LOW_SHARPE"
    assert row[1] == 1


def test_persist_repair_is_idempotent(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    db.initialize_schema()
    kw = dict(
        expression_id="e",
        failure_category="HIGH_TURNOVER",
        failure_detail="t=0.9",
        repair_strategy="lengthen_window_or_add_decay",
        resulting_expression_id=None,
        success=False,
        repair_id="fixed",
    )
    persist_repair(db, **kw)
    persist_repair(db, **kw)
    with sqlite3.connect(str(db.path)) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM repairs WHERE repair_id='fixed'"
        ).fetchone()[0]
    assert count == 1


def test_persist_repair_no_db_returns_id():
    db = SqliteRunLog(None)
    rid = persist_repair(
        db,
        expression_id="e",
        failure_category="LOW_SHARPE",
        failure_detail="",
        repair_strategy="x",
        resulting_expression_id=None,
        success=False,
    )
    assert isinstance(rid, str)
