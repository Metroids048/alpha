"""Tests for L5 MutationEngine (tree_mutation.py)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alpha_mining.mutate.tree_mutation import (
    MutationEngine,
    MutationResult,
    persist_mutation,
    _apply_operator,
    _apply_window,
    _apply_normalization,
    _apply_neutralization,
    _apply_composite,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog


# ─── operator axis ───────────────────────────────────────────────────────────


def test_operator_replaces_first_known_token():
    results = _apply_operator("ts_rank(close, 21)", 2)
    assert len(results) > 0
    for expr, detail in results:
        assert "ts_rank" not in expr or expr.count(
            "ts_rank"
        ) < "ts_rank(close, 21)".count("ts_rank")
        assert "->" in detail


def test_operator_unknown_returns_empty():
    assert _apply_operator("unknown_fn(x, 10)", 2) == []


def test_operator_limit_is_respected():
    results = _apply_operator("group_rank(x, subindustry)", 1)
    assert len(results) <= 1


# ─── window axis ─────────────────────────────────────────────────────────────


def test_window_replaces_numeric_argument():
    results = _apply_window("ts_rank(close, 21)", 2)
    assert len(results) >= 1
    for expr, detail in results:
        assert "21" not in expr
        assert "window 21 ->" in detail


def test_window_no_number_returns_empty():
    assert _apply_window("rank(close)", 2) == []


def test_window_boundary_at_max_only_has_one_neighbour():
    results = _apply_window("ts_rank(close, 252)", 2)
    assert all("252" not in e for e, _ in results)
    assert len(results) >= 1


# ─── normalization axis ───────────────────────────────────────────────────────


def test_normalization_adds_wrapper_not_already_present():
    results = _apply_normalization("ts_rank(close, 21)", 2)
    assert len(results) >= 1
    for expr, detail in results:
        assert expr.startswith(("rank(", "zscore(", "winsorize("))


def test_normalization_skips_existing_wrapper():
    results = _apply_normalization("rank(ts_rank(close, 21))", 2)
    wrappers = [d.split()[-1] for _, d in results]
    assert "rank" not in wrappers


# ─── neutralization axis ──────────────────────────────────────────────────────


def test_neutralization_substitutes_subindustry():
    results = _apply_neutralization("group_rank(x, subindustry)", 2)
    assert len(results) >= 1
    for expr, detail in results:
        assert "subindustry" not in expr
        assert "subindustry ->" in detail


def test_neutralization_no_token_returns_empty():
    assert _apply_neutralization("ts_rank(close, 21)", 2) == []


# ─── composite axis ───────────────────────────────────────────────────────────


def test_composite_combines_with_peers():
    results = _apply_composite(
        "ts_rank(close, 21)", ["ts_rank(volume, 21)", "rank(sales)"], 2
    )
    assert len(results) == 2
    for expr, detail in results:
        assert "+" in expr
        assert "ts_rank(close, 21)" in expr


def test_composite_skips_identical_peer():
    results = _apply_composite("ts_rank(close, 21)", ["ts_rank(close, 21)"], 2)
    assert results == []


# ─── MutationEngine ──────────────────────────────────────────────────────────


def test_engine_mutate_all_axes_returns_results_for_rich_expression():
    engine = MutationEngine(max_per_axis=2)
    expr = "group_rank(ts_rank(close, 21), subindustry)"
    peers = ["ts_rank(volume, 21)"]
    results = engine.mutate_all_axes(expr, peer_exprs=peers)
    axes_hit = {r.axis for r in results}
    assert axes_hit >= {"operator", "window", "neutralization"}
    for r in results:
        assert isinstance(r, MutationResult)
        assert r.mutated_expression != expr or r.axis == "composite"


def test_engine_unknown_axis_raises():
    engine = MutationEngine()
    with pytest.raises(ValueError, match="Unknown mutation axis"):
        engine.mutate("ts_rank(x, 21)", "bad_axis")


def test_engine_max_per_axis_is_respected():
    engine = MutationEngine(max_per_axis=1)
    results = engine.mutate("ts_rank(x, 21)", "operator")
    assert len(results) <= 1


# ─── persist_mutation ─────────────────────────────────────────────────────────


def test_persist_mutation_writes_row(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    db = SqliteRunLog(db_path)
    db.initialize_schema()
    mid = persist_mutation(
        db,
        parent_expression_id="parent-1",
        child_expression_id="child-1",
        axis="window",
        detail="window 21 -> 63",
    )
    with sqlite3.connect(str(db_path)) as con:
        row = con.execute(
            "SELECT mutation_id, parent_expression_id, child_expression_id, mutation_axis, mutation_detail "
            "FROM mutations WHERE mutation_id = ?",
            (mid,),
        ).fetchone()
    assert row is not None
    assert row[3] == "window"
    assert row[4] == "window 21 -> 63"


def test_persist_mutation_is_idempotent(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    db.initialize_schema()
    persist_mutation(
        db,
        parent_expression_id="p",
        child_expression_id="c",
        axis="operator",
        detail="ts_rank -> rank",
        mutation_id="fixed-id",
    )
    persist_mutation(
        db,
        parent_expression_id="p",
        child_expression_id="c",
        axis="operator",
        detail="ts_rank -> rank",
        mutation_id="fixed-id",
    )
    with sqlite3.connect(str(db.path)) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM mutations WHERE mutation_id='fixed-id'"
        ).fetchone()[0]
    assert count == 1


def test_persist_mutation_no_db_returns_id():
    db = SqliteRunLog(None)
    mid = persist_mutation(
        db, parent_expression_id="p", child_expression_id="c", axis="window", detail="x"
    )
    assert isinstance(mid, str) and len(mid) > 0
