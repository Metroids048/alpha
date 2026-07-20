"""Tests for Phase 5: SubmissionJudge + EvolutionEngine."""

from __future__ import annotations

import sqlite3
from pathlib import Path


from alpha_mining.filter.submission_judge import (
    SubmissionJudge,
    JudgeScore,
    _score_novelty,
    _score_data_category,
    _score_operator_diversity,
    _normalize_metric,
)
from alpha_mining.scheduler.evolution import (
    EvolutionEngine,
    recompute_topic_stats,
    update_sampling_weights,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog


def _make_emb(values: list[float]) -> list[float]:
    return values


# ─── scoring helpers ─────────────────────────────────────────────────────────


def test_score_novelty_no_refs_returns_one():
    assert _score_novelty([1.0, 0.0], []) == 1.0


def test_score_novelty_identical_ref_returns_zero():
    emb = [1.0, 0.0]
    score = _score_novelty(emb, [emb])
    assert abs(score) < 1e-6


def test_score_novelty_orthogonal_ref_returns_one():
    score = _score_novelty([1.0, 0.0], [[0.0, 1.0]])
    assert abs(score - 1.0) < 1e-6


def test_score_novelty_none_embedding_returns_one():
    assert _score_novelty(None, [[1.0, 0.0]]) == 1.0


def test_score_data_category_rare_category_gets_higher_score():
    cats = ["fundamental"] * 5 + ["sentiment"]
    s_rare = _score_data_category("sentiment", cats)
    s_common = _score_data_category("fundamental", cats)
    assert s_rare > s_common


def test_score_data_category_none_category_returns_one():
    assert _score_data_category(None, ["fundamental"]) == 1.0


def test_score_operator_diversity_no_overlap_returns_one():
    score = _score_operator_diversity("rank(close)", ["ts_zscore(volume, 21)"])
    assert score > 0.5


def test_score_operator_diversity_identical_returns_zero():
    expr = "ts_rank(close, 21)"
    score = _score_operator_diversity(expr, [expr] * 5)
    assert score < 0.1


def test_normalize_metric_clamps_to_unit_interval():
    assert _normalize_metric(0.5, 1.0, 3.0) == 0.0
    assert _normalize_metric(4.0, 1.0, 3.0) == 1.0
    assert abs(_normalize_metric(2.0, 1.0, 3.0) - 0.5) < 1e-6


def test_normalize_metric_none_returns_zero():
    assert _normalize_metric(None, 1.0, 3.0) == 0.0


# ─── SubmissionJudge ─────────────────────────────────────────────────────────


def test_judge_score_is_weighted_combination():
    judge = SubmissionJudge(
        weights={
            "novelty": 1.0,
            "data_category": 0.0,
            "operator_diversity": 0.0,
            "sharpe_norm": 0.0,
            "fitness_norm": 0.0,
        }
    )
    score = judge.score(
        expression_id="e1",
        expression_text="ts_rank(close, 21)",
        sharpe=1.5,
        fitness=1.0,
        embedding=[1.0, 0.0],
        data_category="fundamental",
        ref_embeddings=[[0.0, 1.0]],  # orthogonal → novelty = 1.0
        ref_expressions=[],
        ref_categories=[],
    )
    assert abs(score.priority_score - 1.0) < 1e-6
    assert abs(score.novelty - 1.0) < 1e-6


def test_judge_rank_orders_by_priority_descending():
    judge = SubmissionJudge()
    scores = [
        JudgeScore("e1", 0.3),
        JudgeScore("e2", 0.9),
        JudgeScore("e3", 0.1),
    ]
    ranked = judge.rank(scores)
    assert [s.expression_id for s in ranked] == ["e2", "e1", "e3"]


def test_judge_persist_score_writes_to_db(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    db.initialize_schema()
    with sqlite3.connect(str(db.path)) as con:
        con.execute(
            "INSERT INTO expressions (expression_id, expression_text, normalized_text, "
            "generation_strategy, generation_layer, created_at) "
            "VALUES ('e1', 'ts_rank(x, 21)', 'ts_rank(x, 21)', 'template', 'L4', '2026-01-01')"
        )
    judge = SubmissionJudge()
    score = JudgeScore("e1", 0.75)
    judge.persist_score(db, score)
    with sqlite3.connect(str(db.path)) as con:
        row = con.execute(
            "SELECT submission_priority_score FROM expressions WHERE expression_id='e1'"
        ).fetchone()
    assert row is not None and abs(row[0] - 0.75) < 1e-6


# ─── EvolutionEngine ─────────────────────────────────────────────────────────


def _seed_db(db: SqliteRunLog) -> None:
    """Populate minimal Research Memory rows for evolution tests."""
    db.initialize_schema()
    with sqlite3.connect(str(db.path)) as con:
        con.execute(
            "INSERT INTO research_topics (topic_id, topic_name_cn, topic_name_en, created_at) "
            "VALUES ('t1', '盈利能力', 'profitability', '2026-01-01')"
        )
        con.execute(
            "INSERT INTO hypotheses (hypothesis_id, topic_id, statement_cn, created_at) "
            "VALUES ('h1', 't1', 'test hypothesis', '2026-01-01')"
        )
        con.execute(
            "INSERT INTO expressions (expression_id, expression_text, normalized_text, "
            "hypothesis_id, generation_strategy, generation_layer, created_at) "
            "VALUES ('e1', 'ts_rank(x, 21)', 'ts_rank(x, 21)', 'h1', 'template', 'L4', '2026-01-01')"
        )
        con.execute(
            "INSERT INTO simulation_runs (expression_id, status, sharpe, fitness, utc_iso) "
            "VALUES ('e1', 'metric_pass', 1.5, 1.2, '2026-01-01')"
        )


def test_recompute_topic_stats_creates_row(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    _seed_db(db)
    count = recompute_topic_stats(db)
    assert count == 1
    with sqlite3.connect(str(db.path)) as con:
        row = con.execute(
            "SELECT pass_rate, avg_sharpe FROM topic_stats WHERE topic_id='t1'"
        ).fetchone()
    assert row is not None
    assert row[0] > 0  # at least one pass
    assert abs(row[1] - 1.5) < 1e-4


def test_update_sampling_weights_sets_positive_weights(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    _seed_db(db)
    recompute_topic_stats(db)
    updated = update_sampling_weights(db)
    assert updated == 1
    with sqlite3.connect(str(db.path)) as con:
        weight = con.execute(
            "SELECT sampling_weight FROM topic_stats WHERE topic_id='t1'"
        ).fetchone()[0]
    assert weight > 0


def test_evolution_engine_run_returns_counts(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    _seed_db(db)
    engine = EvolutionEngine(db)
    result = engine.run()
    assert result["stats_updated"] == 1
    assert result["weights_updated"] == 1


def test_evolution_engine_topic_weights_snapshot(tmp_path: Path):
    db = SqliteRunLog(tmp_path / "test.sqlite")
    _seed_db(db)
    engine = EvolutionEngine(db)
    engine.run()
    weights = engine.topic_weights()
    assert "t1" in weights
    assert weights["t1"] > 0


def test_evolution_engine_no_db_returns_zero():
    db = SqliteRunLog(None)
    engine = EvolutionEngine(db)
    result = engine.run()
    assert result["stats_updated"] == 0
    assert result["weights_updated"] == 0
