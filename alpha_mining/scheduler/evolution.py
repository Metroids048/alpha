"""Evolution Engine — periodic topic_stats recomputation and weight updates."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

from alpha_mining.storage.sqlite_store import SqliteRunLog


def recompute_topic_stats(db: SqliteRunLog) -> int:
    """Recompute topic_stats from expressions + simulation_runs. Returns row count updated."""
    if not db.path:
        return 0
    with sqlite3.connect(str(db.path)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        rows = con.execute(
            """
            SELECT
                t.topic_id,
                COUNT(e.expression_id)                              AS total_generated,
                COUNT(sr.id)                                        AS total_simulated,
                SUM(CASE WHEN sr.status='metric_pass' OR sr.status='submitted' THEN 1 ELSE 0 END)
                                                                    AS total_passed,
                SUM(CASE WHEN sr.status='submitted' THEN 1 ELSE 0 END)
                                                                    AS total_submitted,
                AVG(CASE WHEN sr.sharpe IS NOT NULL THEN sr.sharpe END) AS avg_sharpe,
                AVG(CASE WHEN sr.fitness IS NOT NULL THEN sr.fitness END) AS avg_fitness
            FROM research_topics t
            LEFT JOIN hypotheses h   ON h.topic_id = t.topic_id
            LEFT JOIN expressions e  ON e.hypothesis_id = h.hypothesis_id
            LEFT JOIN simulation_runs sr ON sr.expression_id = e.expression_id
            GROUP BY t.topic_id
            """
        ).fetchall()
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            topic_id, gen, sim, passed, submitted, avg_sh, avg_fi = row
            pass_rate = (passed / sim) if (sim and sim > 0) else 0.0
            con.execute(
                """
                INSERT INTO topic_stats
                    (topic_id, total_generated, total_simulated, total_passed_gate,
                     total_submitted, pass_rate, avg_sharpe, avg_fitness, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    total_generated   = excluded.total_generated,
                    total_simulated   = excluded.total_simulated,
                    total_passed_gate = excluded.total_passed_gate,
                    total_submitted   = excluded.total_submitted,
                    pass_rate         = excluded.pass_rate,
                    avg_sharpe        = excluded.avg_sharpe,
                    avg_fitness       = excluded.avg_fitness,
                    last_updated      = excluded.last_updated
                """,
                (
                    topic_id,
                    gen or 0,
                    sim or 0,
                    passed or 0,
                    submitted or 0,
                    pass_rate,
                    avg_sh,
                    avg_fi,
                    now,
                ),
            )
            updated += 1
    return updated


def update_sampling_weights(db: SqliteRunLog, *, exploration_bonus: float = 1.0) -> int:
    """Update topic_stats.sampling_weight using UCB formula. Returns rows touched."""
    if not db.path:
        return 0
    with sqlite3.connect(str(db.path)) as con:
        rows = con.execute(
            "SELECT topic_id, pass_rate, total_simulated FROM topic_stats"
        ).fetchall()
        if not rows:
            return 0
        total_sims = sum(r[2] for r in rows)
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for topic_id, pass_rate, n_sims in rows:
            n = max(1, n_sims or 1)
            # UCB1: mean + exploration_bonus * sqrt(2 * ln(total) / n)
            ucb = (pass_rate or 0.0) + exploration_bonus * math.sqrt(
                2.0 * math.log(max(1, total_sims)) / n
            )
            con.execute(
                "UPDATE topic_stats SET sampling_weight = ?, last_updated = ? "
                "WHERE topic_id = ?",
                (max(0.01, ucb), now, topic_id),
            )
            updated += 1
    return updated


class EvolutionEngine:
    """Orchestrate periodic topic_stats refresh and UCB weight updates."""

    def __init__(self, db: SqliteRunLog, *, exploration_bonus: float = 1.0) -> None:
        self.db = db
        self.exploration_bonus = exploration_bonus

    def run(self) -> dict[str, int]:
        """Recompute stats then update weights. Returns counts."""
        stats_updated = recompute_topic_stats(self.db)
        weights_updated = update_sampling_weights(
            self.db, exploration_bonus=self.exploration_bonus
        )
        return {"stats_updated": stats_updated, "weights_updated": weights_updated}

    def topic_weights(self) -> dict[str, float]:
        """Return current {topic_id: sampling_weight} snapshot."""
        if not self.db.path:
            return {}
        with sqlite3.connect(str(self.db.path)) as con:
            rows = con.execute(
                "SELECT topic_id, sampling_weight FROM topic_stats"
            ).fetchall()
        return {r[0]: r[1] for r in rows}
