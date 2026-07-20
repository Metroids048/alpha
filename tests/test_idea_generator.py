from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from alpha_mining.generator.idea import IdeaGenerator, InsufficientCategoryCoverage
from alpha_mining.storage.sqlite_store import SqliteRunLog


def _database(tmp_path: Path, rows: list[tuple[str, str, float, int, int]]) -> Path:
    database = tmp_path / "ideas.sqlite3"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        for topic_id, category, weight, simulated, active in rows:
            connection.execute(
                """
                INSERT INTO research_topics (
                    topic_id, topic_name_cn, topic_name_en, category, data_category,
                    description, source, created_at, active
                ) VALUES (?, ?, ?, 'test', ?, ?, 'test', '2026-07-17T00:00:00Z', ?)
                """,
                (topic_id, topic_id, topic_id, category, topic_id, active),
            )
            connection.execute(
                """
                INSERT INTO topic_stats (
                    topic_id, total_simulated, sampling_weight
                ) VALUES (?, ?, ?)
                """,
                (topic_id, simulated, weight),
            )
    return database


def test_select_topics_guarantees_three_data_categories_and_active_only(
    tmp_path: Path,
) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental_a", "fundamental", 5.0, 100, 1),
            ("price_a", "price", 4.0, 100, 1),
            ("analyst_a", "analyst", 3.0, 100, 1),
            ("sentiment_a", "sentiment", 2.0, 100, 1),
            ("inactive_options", "options", 100.0, 100, 0),
        ],
    )
    generator = IdeaGenerator(database, rng=random.Random(7), epsilon=0.0)

    batch = generator.select_topics(4)

    assert len(batch.topic_ids) == 4
    assert len(set(batch.topic_ids)) == 4
    assert len(set(batch.data_categories)) >= 3
    assert "inactive_options" not in batch.topic_ids
    assert batch.cold_start is False
    assert batch.exploratory is False


def test_historical_sampling_weight_biases_selection_within_category(
    tmp_path: Path,
) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental_high", "fundamental", 50.0, 200, 1),
            ("fundamental_low", "fundamental", 0.1, 200, 1),
            ("price", "price", 1.0, 200, 1),
            ("analyst", "analyst", 1.0, 200, 1),
        ],
    )
    generator = IdeaGenerator(database, rng=random.Random(11), epsilon=0.0)
    selected = {"fundamental_high": 0, "fundamental_low": 0}

    for _ in range(200):
        batch = generator.select_topics(3)
        for topic_id in selected:
            selected[topic_id] += int(topic_id in batch.topic_ids)

    assert selected["fundamental_high"] > 190
    assert selected["fundamental_low"] < 10


def test_cold_start_uses_uniform_weights_and_higher_epsilon(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental", "fundamental", 1000.0, 0, 1),
            ("price", "price", 0.01, 0, 1),
            ("analyst", "analyst", 0.01, 0, 1),
        ],
    )
    generator = IdeaGenerator(
        database,
        rng=random.Random(3),
        epsilon=0.0,
        cold_start_epsilon=1.0,
        cold_start_min_simulations=20,
    )

    candidates, cold_start = generator.load_candidates()
    batch = generator.select_topics(3)

    assert cold_start is True
    assert {candidate.sampling_weight for candidate in candidates} == {1.0}
    assert batch.cold_start is True
    assert batch.exploratory is True


def test_explicit_exploration_marks_batch(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental", "fundamental", 1.0, 100, 1),
            ("price", "price", 1.0, 100, 1),
            ("analyst", "analyst", 1.0, 100, 1),
        ],
    )
    batch = IdeaGenerator(database, rng=random.Random(1), epsilon=1.0).select_topics(3)

    assert batch.exploratory is True
    assert batch.cold_start is False


def test_rejects_round_when_three_category_coverage_is_impossible(
    tmp_path: Path,
) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental_a", "fundamental", 1.0, 100, 1),
            ("fundamental_b", "fundamental", 1.0, 100, 1),
            ("price", "price", 1.0, 100, 1),
        ],
    )

    with pytest.raises(InsufficientCategoryCoverage, match="3 active data categories"):
        IdeaGenerator(database, rng=random.Random(1)).select_topics(3)


def test_rejects_batch_size_below_coverage_floor(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        [
            ("fundamental", "fundamental", 1.0, 100, 1),
            ("price", "price", 1.0, 100, 1),
            ("analyst", "analyst", 1.0, 100, 1),
        ],
    )

    with pytest.raises(ValueError, match="at least 3"):
        IdeaGenerator(database).select_topics(2)
