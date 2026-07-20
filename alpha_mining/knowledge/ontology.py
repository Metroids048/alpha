"""Structured seed knowledge for the research planning layers."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SEED_TOPICS_PATH = Path(__file__).with_name("seed_topics.yaml")
_TOPIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class DataCategory(str, Enum):
    PRICE = "price"
    FUNDAMENTAL = "fundamental"
    ANALYST = "analyst"
    SENTIMENT = "sentiment"
    OPTIONS = "options"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class ResearchTopic:
    topic_id: str
    topic_name_cn: str
    topic_name_en: str
    category: str
    data_category: DataCategory
    description: str
    source: str
    source_ref: str = ""
    active: bool = True
    prior_strategy: str | None = None
    prior_sample_count: int | None = None
    prior_pass_rate: float | None = None

    def to_record(self, *, created_at: str) -> dict[str, object]:
        return {
            "topic_id": self.topic_id,
            "topic_name_cn": self.topic_name_cn,
            "topic_name_en": self.topic_name_en,
            "category": self.category,
            "data_category": self.data_category.value,
            "description": self.description,
            "source": self.source,
            "created_at": created_at,
            "active": int(self.active),
        }


@dataclass(frozen=True)
class AlphaFamily:
    family_id: str
    name: str
    pattern: str
    rationale: str
    preferred_data_categories: tuple[DataCategory, ...]


@dataclass(frozen=True)
class HardConstraint:
    constraint_id: str
    description: str
    check_type: str
    applies_to_layer: str


ALPHA_FAMILIES = (
    AlphaFamily(
        "A",
        "relative_value",
        "group_rank(FIELD / cap, subindustry) - 0.5",
        "Size-normalized peer ranking promotes breadth, centering, and lower concentration.",
        (DataCategory.FUNDAMENTAL,),
    ),
    AlphaFamily(
        "B",
        "fundamental_improvement",
        "group_rank(ts_delta(FIELD, 63) / cap, subindustry) - 0.5",
        "Measures improving fundamentals with a shallow transform and peer comparison.",
        (DataCategory.FUNDAMENTAL, DataCategory.HYBRID),
    ),
    AlphaFamily(
        "C",
        "time_series_strength",
        "ts_rank(FIELD, 252) - 0.5",
        "Converts a slow series into a centered strength score without deep smoothing.",
        (DataCategory.FUNDAMENTAL, DataCategory.ANALYST),
    ),
    AlphaFamily(
        "D",
        "price_reversion",
        "-rank(ts_delta(close, N))",
        "Captures broad short-horizon reversal using a direct, shallow price signal.",
        (DataCategory.PRICE,),
    ),
    AlphaFamily(
        "E",
        "price_relative_value",
        "zscore(vwap / close)",
        "Represents intraday price deviation with an optional single range qualifier.",
        (DataCategory.PRICE,),
    ),
    AlphaFamily(
        "F",
        "hybrid",
        "group_rank(FUNDAMENTAL / cap, subindustry) - 0.5 + -rank(ts_delta(close, 5))",
        "Combines one fundamental component with one price component only when needed.",
        (DataCategory.HYBRID,),
    ),
)


HARD_CONSTRAINTS = (
    HardConstraint(
        "center_output",
        "Expression output must be centered rather than structurally positive-only.",
        "expression_ast",
        "L4",
    ),
    HardConstraint(
        "preserve_breadth",
        "Avoid rare hard gates and sparse conditions unless explicitly justified.",
        "expression_ast",
        "L4",
    ),
    HardConstraint(
        "shallow_fundamentals",
        "Low-frequency fundamental fields must not use deeply nested smoothing.",
        "field_frequency",
        "L4",
    ),
    HardConstraint(
        "bounded_fundamental_windows",
        "Fundamental changes should prefer 21, 42, 63, or 126 day windows over blind 252-day deltas.",
        "window_policy",
        "L4",
    ),
    HardConstraint(
        "limit_complexity",
        "Prefer the simplest family that represents the hypothesis; avoid novelty-only operator chains.",
        "complexity_budget",
        "L4",
    ),
    HardConstraint(
        "normalize_scale",
        "Cross-sectional fundamental comparisons require a defensible scale normalizer such as cap or assets.",
        "data_mapping",
        "L3",
    ),
    HardConstraint(
        "peer_neutralization",
        "Use sector, industry, or subindustry grouping when the hypothesis is peer-relative.",
        "neutralization_policy",
        "L3/L4",
    ),
    HardConstraint(
        "align_delay_frequency",
        "Delay and horizon must match data freshness; slow fundamentals are not Delay-0 event signals.",
        "settings_policy",
        "L3/L4",
    ),
)


def _optional_prior(raw: dict[str, Any]) -> tuple[str | None, int | None, float | None]:
    strategy = raw.get("prior_strategy")
    sample_count = raw.get("prior_sample_count")
    pass_rate = raw.get("prior_pass_rate")
    present = (strategy is not None, sample_count is not None, pass_rate is not None)
    if any(present) and not all(present):
        raise ValueError("topic prior fields must be provided together")
    if not any(present):
        return None, None, None
    assert strategy is not None and sample_count is not None and pass_rate is not None
    count = int(sample_count)
    rate = float(pass_rate)
    if count < 1 or not 0.0 <= rate <= 1.0:
        raise ValueError("topic prior sample count/pass rate is invalid")
    return str(strategy), count, rate


def load_seed_topics(path: str | Path | None = None) -> tuple[ResearchTopic, ...]:
    source_path = Path(path) if path else DEFAULT_SEED_TOPICS_PATH
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    raw_topics = payload.get("topics") if isinstance(payload, dict) else None
    if not isinstance(raw_topics, list):
        raise ValueError("seed topic YAML must contain a topics list")

    topics: list[ResearchTopic] = []
    seen: set[str] = set()
    for raw in raw_topics:
        if not isinstance(raw, dict):
            raise ValueError("each seed topic must be a mapping")
        topic_id = str(raw.get("topic_id") or "").strip()
        if not _TOPIC_ID_RE.fullmatch(topic_id) or topic_id in seen:
            raise ValueError(f"invalid or duplicate topic_id: {topic_id!r}")
        seen.add(topic_id)
        prior_strategy, prior_sample_count, prior_pass_rate = _optional_prior(raw)
        topic = ResearchTopic(
            topic_id=topic_id,
            topic_name_cn=str(raw.get("topic_name_cn") or "").strip(),
            topic_name_en=str(raw.get("topic_name_en") or "").strip(),
            category=str(raw.get("category") or "").strip(),
            data_category=DataCategory(str(raw.get("data_category") or "").strip()),
            description=str(raw.get("description") or "").strip(),
            source=str(raw.get("source") or "seed").strip(),
            source_ref=str(raw.get("source_ref") or "").strip(),
            active=bool(raw.get("active", True)),
            prior_strategy=prior_strategy,
            prior_sample_count=prior_sample_count,
            prior_pass_rate=prior_pass_rate,
        )
        if not all(
            (
                topic.topic_name_cn,
                topic.topic_name_en,
                topic.category,
                topic.description,
                topic.source,
            )
        ):
            raise ValueError(f"seed topic {topic_id!r} is missing required text")
        topics.append(topic)
    if not 15 <= len(topics) <= 25:
        raise ValueError("Knowledge Layer requires 15 to 25 seed topics")
    return tuple(topics)


def install_seed_topics(
    database: str | Path,
    *,
    topics: tuple[ResearchTopic, ...] | None = None,
    created_at: str | None = None,
) -> int:
    """Idempotently install reviewed topics and initialize their sampling statistics."""
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database_path = Path(database).expanduser().resolve()
    installed_topics = topics or load_seed_topics()
    timestamp = created_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    SqliteRunLog(database_path).initialize_schema()
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for topic in installed_topics:
            record = topic.to_record(created_at=timestamp)
            connection.execute(
                """
                INSERT INTO research_topics (
                    topic_id, topic_name_cn, topic_name_en, category, data_category,
                    description, source, created_at, active
                ) VALUES (
                    :topic_id, :topic_name_cn, :topic_name_en, :category, :data_category,
                    :description, :source, :created_at, :active
                )
                ON CONFLICT(topic_id) DO UPDATE SET
                    topic_name_cn=excluded.topic_name_cn,
                    topic_name_en=excluded.topic_name_en,
                    category=excluded.category,
                    data_category=excluded.data_category,
                    description=excluded.description,
                    source=excluded.source,
                    active=excluded.active
                """,
                record,
            )

            prior = None
            if topic.prior_strategy:
                prior = connection.execute(
                    """
                    SELECT s.total_generated, s.total_simulated, s.total_passed_gate,
                           s.total_submitted, s.pass_rate, s.avg_sharpe, s.avg_fitness,
                           s.avg_self_corr, s.sampling_weight
                    FROM research_topics legacy
                    JOIN topic_stats s ON s.topic_id = legacy.topic_id
                    WHERE legacy.source = 'legacy_backfill'
                      AND legacy.topic_name_en = ?
                    LIMIT 1
                    """,
                    (topic.prior_strategy,),
                ).fetchone()
            if prior is None:
                generated = int(topic.prior_sample_count or 0)
                pass_rate = float(topic.prior_pass_rate or 0.0)
                prior = (
                    generated,
                    generated,
                    round(generated * pass_rate),
                    0,
                    pass_rate,
                    None,
                    None,
                    None,
                    1.0,
                )
            connection.execute(
                """
                INSERT INTO topic_stats (
                    topic_id, total_generated, total_simulated, total_passed_gate,
                    total_submitted, pass_rate, avg_sharpe, avg_fitness,
                    avg_self_corr, sampling_weight, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO NOTHING
                """,
                (topic.topic_id, *prior, timestamp),
            )
        connection.commit()
    return len(installed_topics)
