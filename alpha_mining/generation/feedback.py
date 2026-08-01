"""Authoritative candidate outcome feedback store.

One terminal outcome per request_hash. Idempotent on repeated writes
(first-write wins). UNKNOWN is never overwritten by FAILED.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_VALID_OUTCOMES = frozenset(("PASS", "NEAR_PASS", "FAR_FAIL", "FAILED", "UNKNOWN"))


class CandidateFeedbackStore:
    """Persist simulation outcomes for feedback-driven candidate generation."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS candidate_outcomes (
                    request_hash TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL DEFAULT '',
                    topic_id TEXT NOT NULL DEFAULT '',
                    hypothesis_id TEXT NOT NULL DEFAULT '',
                    research_family TEXT NOT NULL DEFAULT '',
                    strategy_family TEXT NOT NULL DEFAULT '',
                    mechanism TEXT NOT NULL DEFAULT '',
                    dataset TEXT NOT NULL DEFAULT '',
                    parent_template TEXT NOT NULL DEFAULT '',
                    exact_hash TEXT NOT NULL DEFAULT '',
                    parameter_skeleton TEXT NOT NULL DEFAULT '',
                    field_skeleton TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL,
                    sharpe REAL,
                    fitness REAL,
                    turnover REAL,
                    checks_json TEXT NOT NULL DEFAULT '[]',
                    error_category TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_co_topic ON candidate_outcomes(topic_id)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_co_family ON candidate_outcomes(strategy_family)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_co_skeleton ON candidate_outcomes(field_skeleton)"
            )

    def record(
        self,
        request_hash: str,
        outcome: str,
        *,
        sharpe: float | None = None,
        fitness: float | None = None,
        turnover: float | None = None,
        field_skeleton: str = "",
        strategy_family: str = "",
        topic_id: str = "",
        candidate_id: str = "",
        hypothesis_id: str = "",
        research_family: str = "",
        mechanism: str = "",
        dataset: str = "",
        parent_template: str = "",
        exact_hash: str = "",
        parameter_skeleton: str = "",
        checks: list[Any] | None = None,
        error_category: str = "",
        error_message: str = "",
    ) -> None:
        if outcome not in _VALID_OUTCOMES:
            raise ValueError(f"Invalid outcome {outcome!r}; must be one of {sorted(_VALID_OUTCOMES)}")
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT OR IGNORE INTO candidate_outcomes
                   (request_hash, candidate_id, topic_id, hypothesis_id, research_family,
                    strategy_family, mechanism, dataset, parent_template, exact_hash,
                    parameter_skeleton, field_skeleton, outcome, sharpe, fitness, turnover,
                    checks_json, error_category, error_message, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_hash, candidate_id, topic_id, hypothesis_id, research_family,
                    strategy_family, mechanism, dataset, parent_template, exact_hash,
                    parameter_skeleton, field_skeleton, outcome, sharpe, fitness, turnover,
                    json.dumps(checks or []), error_category, error_message, now,
                ),
            )

    def outcomes_for_request(self, request_hash: str) -> str | None:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT outcome FROM candidate_outcomes WHERE request_hash=?",
                (request_hash,),
            ).fetchone()
        return row[0] if row else None

    def family_pass_rate(self, strategy_family: str) -> float:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN outcome='PASS' THEN 1 ELSE 0 END)
                   FROM candidate_outcomes WHERE strategy_family=?""",
                (strategy_family,),
            ).fetchone()
        if not row or not row[0]:
            return 0.0
        return float(row[1] or 0) / float(row[0])
