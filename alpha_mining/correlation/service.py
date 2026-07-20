"""Level-2 aligned returns correlation calculations."""

from __future__ import annotations

import math
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    ml, mr = sum(left) / len(left), sum(right) / len(right)
    dl, dr = [v - ml for v in left], [v - mr for v in right]
    denominator = math.sqrt(sum(v * v for v in dl) * sum(v * v for v in dr))
    return sum(a * b for a, b in zip(dl, dr)) / denominator if denominator else None


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = (index + end - 1) / 2 + 1
        for cursor in range(index, end):
            ranks[indexed[cursor][0]] = rank
        index = end
    return ranks


@dataclass(frozen=True)
class CorrelationResult:
    status: str
    overlap: int
    pearson: float | None
    spearman: float | None
    absolute_correlation: float | None
    behavior_risk: bool


class CorrelationService:
    def __init__(self, *, min_overlap: int = 60, internal_limit: float = 0.65) -> None:
        self.min_overlap = max(2, int(min_overlap))
        self.internal_limit = float(internal_limit)

    def compare(
        self, candidate: list[tuple[str, float]], reference: list[tuple[str, float]]
    ) -> CorrelationResult:
        left, right = dict(candidate), dict(reference)
        dates = sorted(set(left) & set(right))
        if len(dates) < self.min_overlap:
            return CorrelationResult(
                "INSUFFICIENT_HISTORY", len(dates), None, None, None, False
            )
        a, b = [float(left[d]) for d in dates], [float(right[d]) for d in dates]
        pearson, spearman = _pearson(a, b), _pearson(_ranks(a), _ranks(b))
        valid = [
            abs(value)
            for value in (pearson, spearman)
            if value is not None and math.isfinite(value)
        ]
        if not valid:
            return CorrelationResult(
                "INSUFFICIENT_HISTORY", len(dates), pearson, spearman, None, False
            )
        absolute = max(valid)
        behavior_risk = absolute >= self.internal_limit
        return CorrelationResult(
            "FAIL" if behavior_risk else "PASS",
            len(dates),
            pearson,
            spearman,
            absolute,
            behavior_risk,
        )

    def inspect_sets(
        self,
        candidate: list[tuple[str, float]],
        reference_sets: dict[str, dict[str, list[tuple[str, float]]]],
    ) -> dict[str, dict]:
        output = {}
        for set_name, references in reference_sets.items():
            compared = [
                (reference_id, self.compare(candidate, returns))
                for reference_id, returns in references.items()
            ]
            sufficient = [
                item for item in compared if item[1].absolute_correlation is not None
            ]
            best = (
                max(
                    sufficient,
                    key=lambda item: (
                        item[1].absolute_correlation
                        if item[1].absolute_correlation is not None
                        else -1.0
                    ),
                )
                if sufficient
                else None
            )
            output[set_name] = {
                "max_reference_id": best[0] if best else "",
                "max_absolute_correlation": best[1].absolute_correlation
                if best
                else None,
                "status": best[1].status if best else "INSUFFICIENT_HISTORY",
                "comparisons": len(compared),
            }
        return output

    def persist(
        self,
        database: str | Path,
        *,
        expression_id: str,
        reference_id: str,
        reference_set: str,
        result: CorrelationResult,
    ) -> str:
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result_id = hashlib.sha256(
            f"{expression_id}\0{reference_id}\0{reference_set}\0{created_at}".encode()
        ).hexdigest()
        with sqlite3.connect(database) as con:
            con.execute(
                """INSERT INTO alpha_correlation_results(result_id,expression_id,reference_id,reference_set,overlap,pearson,spearman,absolute_correlation,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    result_id,
                    expression_id,
                    reference_id,
                    reference_set,
                    result.overlap,
                    result.pearson,
                    result.spearman,
                    result.absolute_correlation,
                    result.status,
                    created_at,
                ),
            )
        return result_id
