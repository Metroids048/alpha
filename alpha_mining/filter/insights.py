"""Generator feedback insights derived from recent platform repair records."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_WINDOW = 50
_MAX_CONCEPTS = 8

_OPERATOR_TOKENS: frozenset[str] = frozenset(
    {
        "add",
        "sub",
        "multiply",
        "divide",
        "rank",
        "zscore",
        "market",
        "group_neutralize",
        "group_rank",
        "winsorize",
        "ts_rank",
        "ts_zscore",
        "ts_delta",
        "ts_mean",
        "ts_std",
        "ts_sum",
        "ts_min",
        "ts_max",
        "log",
        "sign",
        "abs",
        "min",
        "max",
        "pow",
        "exp",
        "cap",
        "floor",
        "normalize",
        "regression_neut",
        "truncate",
        "indneutralize",
        "pasteurize",
        "vec_avg",
        "vec_stddev",
        "vec_sum",
        "vec_ir",
        "subindustry",
        "industry",
        "sector",
        "country",
    }
)


@dataclass(frozen=True)
class RepairInsights:
    """Avoidance hints for the hypothesis generator derived from recent failures."""

    avoided_data_concepts: tuple[str, ...]
    preferred_horizon: str | None  # "medium" or "long" when IS_LADDER_SHARPE dominates


def _field_tokens(expression: str) -> list[str]:
    tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*\b", expression)
    return [t for t in tokens if t.lower() not in _OPERATOR_TOKENS][:_MAX_CONCEPTS]


def load_repair_insights(
    db_path: str | Path,
    *,
    window: int = _WINDOW,
) -> RepairInsights:
    """Return avoidance hints based on the most recent repair records in the DB.

    Returns empty insights when the database is absent or the repairs table is empty.
    Never raises — generator callers must not fail because of missing insight data.
    """
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return RepairInsights(avoided_data_concepts=(), preferred_horizon=None)

    try:
        with sqlite3.connect(str(path)) as con:
            rows = con.execute(
                """
                SELECT r.failure_category, e.expression_text
                FROM repairs r
                JOIN expressions e ON e.expression_id = r.expression_id
                WHERE r.failure_category IN ('PROD_CORRELATION', 'IS_LADDER_SHARPE')
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (window,),
            ).fetchall()
    except sqlite3.Error:
        return RepairInsights(avoided_data_concepts=(), preferred_horizon=None)

    seen: set[str] = set()
    prod_concepts: list[str] = []
    ladder_count = 0

    for category, expression in rows:
        if category == "PROD_CORRELATION":
            for token in _field_tokens(str(expression or "")):
                if token not in seen and len(prod_concepts) < _MAX_CONCEPTS:
                    prod_concepts.append(token)
                    seen.add(token)
        elif category == "IS_LADDER_SHARPE":
            ladder_count += 1

    preferred_horizon: str | None = None
    if ladder_count >= 3:
        preferred_horizon = "medium"
    if ladder_count >= 6:
        preferred_horizon = "long"

    return RepairInsights(
        avoided_data_concepts=tuple(prod_concepts),
        preferred_horizon=preferred_horizon,
    )
