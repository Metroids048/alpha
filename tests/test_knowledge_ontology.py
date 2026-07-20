from __future__ import annotations

from dataclasses import asdict
import sqlite3
from pathlib import Path

from alpha_mining.knowledge.ontology import (
    ALPHA_FAMILIES,
    HARD_CONSTRAINTS,
    DataCategory,
    install_seed_topics,
    load_seed_topics,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog


def test_seed_topics_are_unique_complete_and_within_target_count() -> None:
    topics = load_seed_topics()

    assert 15 <= len(topics) <= 25
    assert len({topic.topic_id for topic in topics}) == len(topics)
    assert all(topic.topic_name_cn and topic.topic_name_en for topic in topics)
    assert all(
        topic.description and topic.category and topic.source for topic in topics
    )
    assert all(topic.active for topic in topics)


def test_seed_topics_cover_every_phase_two_data_category() -> None:
    topics = load_seed_topics()
    categories = {topic.data_category for topic in topics}

    assert categories == set(DataCategory)
    assert sum(topic.data_category is DataCategory.FUNDAMENTAL for topic in topics) >= 8
    assert sum(topic.data_category is DataCategory.HYBRID for topic in topics) >= 2


def test_exactly_two_topics_are_human_reviewed_paper_seeds() -> None:
    paper_topics = [
        topic for topic in load_seed_topics() if topic.source == "paper_derived"
    ]

    assert {topic.topic_id for topic in paper_topics} == {
        "short_horizon_price_reversal",
        "intraday_momentum_industry_neutral",
    }
    assert all("ssrn-2701346" in topic.source_ref for topic in paper_topics)


def test_phase_one_high_yield_strategy_is_carried_as_prior_evidence() -> None:
    topics = {topic.topic_id: topic for topic in load_seed_topics()}
    hybrid = topics["fundamental_improvement_price_confirmation"]

    assert hybrid.prior_strategy == "arch_hybrid_delta_pv"
    assert hybrid.prior_sample_count == 73
    assert hybrid.prior_pass_rate == 0.479452


def test_topic_database_records_match_research_topics_schema() -> None:
    topic = load_seed_topics()[0]
    record = topic.to_record(created_at="2026-07-17T00:00:00Z")

    assert set(record) == {
        "topic_id",
        "topic_name_cn",
        "topic_name_en",
        "category",
        "data_category",
        "description",
        "source",
        "created_at",
        "active",
    }
    assert record["data_category"] in {category.value for category in DataCategory}
    assert record["active"] == 1


def test_family_library_contains_structured_family_a_through_f() -> None:
    assert tuple(family.family_id for family in ALPHA_FAMILIES) == tuple("ABCDEF")
    assert all(family.pattern and family.rationale for family in ALPHA_FAMILIES)
    assert ALPHA_FAMILIES[0].pattern == "group_rank(FIELD / cap, subindustry) - 0.5"


def test_eight_hard_constraints_are_structured_and_unique() -> None:
    assert len(HARD_CONSTRAINTS) == 8
    assert len({constraint.constraint_id for constraint in HARD_CONSTRAINTS}) == 8
    assert all(constraint.description for constraint in HARD_CONSTRAINTS)
    assert all(constraint.check_type for constraint in HARD_CONSTRAINTS)
    assert all(constraint.applies_to_layer for constraint in HARD_CONSTRAINTS)
    assert all(
        set(asdict(constraint))
        == {"constraint_id", "description", "check_type", "applies_to_layer"}
        for constraint in HARD_CONSTRAINTS
    )


def test_install_seed_topics_is_idempotent_and_copies_phase_one_prior(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge.sqlite3"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO research_topics (
                topic_id, topic_name_cn, topic_name_en, category, data_category,
                description, source, created_at, active
            ) VALUES (
                'legacy_hybrid', 'arch_hybrid_delta_pv', 'arch_hybrid_delta_pv',
                'legacy_strategy', NULL, 'legacy', 'legacy_backfill',
                '2026-07-17T00:00:00Z', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO topic_stats (
                topic_id, total_generated, total_simulated, total_passed_gate,
                pass_rate, avg_sharpe, avg_fitness, sampling_weight, last_updated
            ) VALUES ('legacy_hybrid', 73, 73, 35, 0.479452, 1.150548, 0.888630, 1.4,
                      '2026-07-17T00:00:00Z')
            """
        )

    first = install_seed_topics(database, created_at="2026-07-17T01:00:00Z")
    second = install_seed_topics(database, created_at="2026-07-17T02:00:00Z")

    assert first == 23
    assert second == 23
    with sqlite3.connect(database) as connection:
        seed_count = connection.execute(
            "SELECT COUNT(*) FROM research_topics WHERE source IN ('seed', 'paper_derived')"
        ).fetchone()[0]
        assert seed_count == 23
        copied = connection.execute(
            """
            SELECT total_generated, total_simulated, total_passed_gate,
                   pass_rate, avg_sharpe, avg_fitness, sampling_weight
            FROM topic_stats
            WHERE topic_id = 'fundamental_improvement_price_confirmation'
            """
        ).fetchone()
        assert copied == (73, 73, 35, 0.479452, 1.150548, 0.888630, 1.4)
