"""L1 research-topic sampling with exploration and category coverage."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar


class InsufficientCategoryCoverage(RuntimeError):
    """The active topic pool cannot satisfy the three-category round constraint."""


@dataclass(frozen=True)
class IdeaCandidate:
    topic_id: str
    data_category: str
    sampling_weight: float
    total_simulated: int


@dataclass(frozen=True)
class IdeaBatch:
    topic_ids: tuple[str, ...]
    data_categories: tuple[str, ...]
    exploratory: bool
    cold_start: bool


T = TypeVar("T")


def _weighted_pop(
    items: list[T],
    weight: Callable[[T], float],
    rng: random.Random,
) -> T:
    if not items:
        raise ValueError("cannot sample from an empty pool")
    weights = [max(0.0, float(weight(item))) for item in items]
    total = sum(weights)
    if total <= 0:
        index = rng.randrange(len(items))
    else:
        target = rng.random() * total
        cumulative = 0.0
        index = len(items) - 1
        for candidate_index, candidate_weight in enumerate(weights):
            cumulative += candidate_weight
            if target < cumulative:
                index = candidate_index
                break
    return items.pop(index)


class IdeaGenerator:
    def __init__(
        self,
        database: str | Path,
        *,
        rng: random.Random | None = None,
        epsilon: float = 0.1,
        cold_start_epsilon: float = 0.2,
        cold_start_min_simulations: int = 20,
    ) -> None:
        if not 0.0 <= epsilon <= 1.0 or not 0.0 <= cold_start_epsilon <= 1.0:
            raise ValueError("epsilon values must be between 0 and 1")
        if cold_start_min_simulations < 0:
            raise ValueError("cold_start_min_simulations must not be negative")
        self.database = Path(database).expanduser().resolve()
        self.rng = rng or random.Random()
        self.epsilon = float(epsilon)
        self.cold_start_epsilon = float(cold_start_epsilon)
        self.cold_start_min_simulations = int(cold_start_min_simulations)

    def load_candidates(self) -> tuple[list[IdeaCandidate], bool]:
        with sqlite3.connect(str(self.database)) as connection:
            rows = connection.execute(
                """
                SELECT t.topic_id, t.data_category,
                       COALESCE(s.sampling_weight, 1.0),
                       COALESCE(s.total_simulated, 0)
                FROM research_topics t
                LEFT JOIN topic_stats s ON s.topic_id = t.topic_id
                WHERE t.active = 1
                  AND t.data_category IS NOT NULL
                  AND TRIM(t.data_category) <> ''
                ORDER BY t.topic_id
                """
            ).fetchall()
        candidates = [
            IdeaCandidate(
                topic_id=str(topic_id),
                data_category=str(data_category),
                sampling_weight=max(0.0, float(sampling_weight)),
                total_simulated=max(0, int(total_simulated)),
            )
            for topic_id, data_category, sampling_weight, total_simulated in rows
        ]
        cold_start = (
            sum(candidate.total_simulated for candidate in candidates)
            < self.cold_start_min_simulations
        )
        if cold_start:
            candidates = [
                IdeaCandidate(
                    candidate.topic_id,
                    candidate.data_category,
                    1.0,
                    candidate.total_simulated,
                )
                for candidate in candidates
            ]
        return candidates, cold_start

    def select_topics(self, count: int = 3) -> IdeaBatch:
        if count < 3:
            raise ValueError("idea batches must contain at least 3 topics")
        candidates, cold_start = self.load_candidates()
        if count > len(candidates):
            raise ValueError(
                f"requested {count} topics from only {len(candidates)} active topics"
            )

        by_category: dict[str, list[IdeaCandidate]] = {}
        for candidate in candidates:
            by_category.setdefault(candidate.data_category, []).append(candidate)
        if len(by_category) < 3:
            raise InsufficientCategoryCoverage(
                "idea generation requires at least 3 active data categories"
            )

        epsilon = self.cold_start_epsilon if cold_start else self.epsilon
        exploratory = self.rng.random() < epsilon

        def candidate_weight(candidate: IdeaCandidate) -> float:
            return 1.0 if exploratory else candidate.sampling_weight

        category_pool = list(by_category.items())
        selected: list[IdeaCandidate] = []
        for _ in range(3):
            category, category_candidates = _weighted_pop(
                category_pool,
                lambda item: sum(candidate_weight(candidate) for candidate in item[1]),
                self.rng,
            )
            del category
            selected.append(
                _weighted_pop(list(category_candidates), candidate_weight, self.rng)
            )

        selected_ids = {candidate.topic_id for candidate in selected}
        remaining = [
            candidate
            for candidate in candidates
            if candidate.topic_id not in selected_ids
        ]
        while len(selected) < count:
            selected.append(_weighted_pop(remaining, candidate_weight, self.rng))

        return IdeaBatch(
            topic_ids=tuple(candidate.topic_id for candidate in selected),
            data_categories=tuple(candidate.data_category for candidate in selected),
            exploratory=exploratory,
            cold_start=cold_start,
        )
