"""Explicit, conservative maintenance operations for the research database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class CleanupResult:
    deleted_expressions: int
    deleted_identities: int
    retained_expressions: int


def clean_stale_expressions(
    db_path: str | Path = "alpha_state.sqlite3",
    *,
    stale_before: datetime | None = None,
) -> CleanupResult:
    """Delete old, unreferenced expressions without creating a missing database."""

    target = Path(db_path)
    if not target.is_file():
        raise FileNotFoundError(target)
    cutoff = stale_before or datetime.now(timezone.utc) - timedelta(hours=24)
    if cutoff.tzinfo is None:
        raise ValueError("stale_before must include a timezone")
    cutoff_text = cutoff.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TEMP TABLE stale_expression_ids(
                   expression_id TEXT PRIMARY KEY
               )"""
        )
        connection.execute(
            """INSERT INTO stale_expression_ids(expression_id)
               SELECT e.expression_id
               FROM expressions e
               WHERE e.created_at < ?
                 AND NOT EXISTS (
                     SELECT 1 FROM simulation_runs r
                     WHERE r.expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM submission_observations o
                     WHERE o.expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM mutations m
                     WHERE m.parent_expression_id=e.expression_id
                        OR m.child_expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM repairs r
                     WHERE r.expression_id=e.expression_id
                        OR r.resulting_expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM expressions child
                     WHERE child.parent_expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM settings_trials t
                     WHERE t.expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM alpha_daily_returns d
                     WHERE d.expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM alpha_correlation_results c
                     WHERE c.expression_id=e.expression_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM consultant_submit_queue q
                     WHERE q.expression_id=e.expression_id
                 )""",
            (cutoff_text,),
        )
        deleted_identities = connection.execute(
            """DELETE FROM expression_identities
               WHERE expression_id IN (SELECT expression_id FROM stale_expression_ids)"""
        ).rowcount
        deleted_expressions = connection.execute(
            """DELETE FROM expressions
               WHERE expression_id IN (SELECT expression_id FROM stale_expression_ids)"""
        ).rowcount
        retained_expressions = int(
            connection.execute("SELECT COUNT(*) FROM expressions").fetchone()[0]
        )
        connection.commit()
    return CleanupResult(
        deleted_expressions=deleted_expressions,
        deleted_identities=deleted_identities,
        retained_expressions=retained_expressions,
    )
