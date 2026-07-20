"""Append-only SQLite log for simulation rows (optional)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SqliteRunLog:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        }

    @classmethod
    def _add_column_if_missing(
        cls,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        if column not in cls._column_names(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def initialize_schema(self) -> None:
        """Create and compatibly migrate the Chapter 4 Research Memory schema."""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    utc_iso TEXT,
                    alpha_id TEXT,
                    expression TEXT,
                    status TEXT,
                    queue_status TEXT,
                    sharpe REAL,
                    fitness REAL,
                    turnover REAL,
                    fail_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS research_topics (
                    topic_id TEXT PRIMARY KEY,
                    topic_name_cn TEXT NOT NULL,
                    topic_name_en TEXT NOT NULL,
                    category TEXT,
                    data_category TEXT,
                    description TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS hypotheses (
                    hypothesis_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL REFERENCES research_topics(topic_id),
                    statement_cn TEXT NOT NULL,
                    statement_en TEXT,
                    mechanism TEXT,
                    horizon TEXT,
                    embedding BLOB,
                    created_at TEXT NOT NULL,
                    llm_model TEXT,
                    status TEXT DEFAULT 'active'
                );

                CREATE TABLE IF NOT EXISTS data_mappings (
                    mapping_id TEXT PRIMARY KEY,
                    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
                    data_field TEXT NOT NULL,
                    dataset_id TEXT,
                    rationale TEXT,
                    field_quality_score REAL,
                    selected_by TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS expressions (
                    expression_id TEXT PRIMARY KEY,
                    expression_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    structure_sig TEXT,
                    hypothesis_id TEXT REFERENCES hypotheses(hypothesis_id),
                    parent_expression_id TEXT REFERENCES expressions(expression_id),
                    generation_strategy TEXT NOT NULL,
                    generation_layer TEXT NOT NULL,
                    embedding BLOB,
                    created_at TEXT NOT NULL,
                    submission_priority_score REAL,
                    novelty_score REAL
                );

                CREATE TABLE IF NOT EXISTS mutations (
                    mutation_id TEXT PRIMARY KEY,
                    parent_expression_id TEXT NOT NULL REFERENCES expressions(expression_id),
                    child_expression_id TEXT NOT NULL REFERENCES expressions(expression_id),
                    mutation_axis TEXT NOT NULL,
                    mutation_detail TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repairs (
                    repair_id TEXT PRIMARY KEY,
                    expression_id TEXT NOT NULL REFERENCES expressions(expression_id),
                    failure_category TEXT NOT NULL,
                    failure_detail TEXT,
                    repair_strategy TEXT NOT NULL,
                    resulting_expression_id TEXT REFERENCES expressions(expression_id),
                    success INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS submission_observations (
                    observation_id TEXT PRIMARY KEY,
                    expression_id TEXT NOT NULL,
                    alpha_id TEXT NOT NULL DEFAULT '',
                    check_digest TEXT NOT NULL,
                    check_passed INTEGER,
                    queue_status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    failure_categories_json TEXT NOT NULL,
                    recommended_actions_json TEXT NOT NULL,
                    description_text TEXT,
                    description_source TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(expression_id, alpha_id, check_digest)
                );

                CREATE TABLE IF NOT EXISTS hypotheses_staging (
                    staging_id TEXT PRIMARY KEY,
                    topic_id TEXT REFERENCES research_topics(topic_id),
                    statement_cn TEXT NOT NULL,
                    mechanism TEXT,
                    source_url TEXT,
                    review_status TEXT DEFAULT 'pending',
                    trial_pass_rate REAL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topic_stats (
                    topic_id TEXT PRIMARY KEY REFERENCES research_topics(topic_id),
                    total_generated INTEGER DEFAULT 0,
                    total_simulated INTEGER DEFAULT 0,
                    total_passed_gate INTEGER DEFAULT 0,
                    total_submitted INTEGER DEFAULT 0,
                    pass_rate REAL DEFAULT 0,
                    avg_sharpe REAL,
                    avg_fitness REAL,
                    avg_self_corr REAL,
                    sampling_weight REAL DEFAULT 1.0,
                    last_updated TEXT
                );
                """
            )

            self._add_column_if_missing(
                con, "expressions", "submission_priority_score", "REAL"
            )
            self._add_column_if_missing(con, "expressions", "novelty_score", "REAL")

            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_returns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alpha_id TEXT NOT NULL,
                    expression_text TEXT,
                    date TEXT NOT NULL,
                    daily_return REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(alpha_id, date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_returns_alpha_id
                    ON daily_returns(alpha_id);
                """
            )

            simulation_extensions = (
                ("expression_id", "TEXT REFERENCES expressions(expression_id)"),
                ("region", "TEXT"),
                ("universe", "TEXT"),
                ("neutralization", "TEXT"),
                ("decay", "INTEGER"),
                ("delay", "INTEGER"),
                ("correlation_max", "REAL"),
            )
            for column, declaration in simulation_extensions:
                self._add_column_if_missing(con, "simulation_runs", column, declaration)
            con.commit()
        finally:
            con.close()
        from alpha_mining.storage.migrations import migrate

        migrate(self.path)

    def store_daily_returns(
        self,
        alpha_id: str,
        expression_text: str,
        returns: list[tuple[str, float]],
        *,
        created_at: str = "",
    ) -> None:
        """Persist (date, return) pairs for an alpha.  Ignores duplicates (UNIQUE on alpha_id+date)."""
        if not self.path or not returns:
            return
        from datetime import datetime, timezone

        ts = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_returns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alpha_id TEXT NOT NULL,
                    expression_text TEXT,
                    date TEXT NOT NULL,
                    daily_return REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(alpha_id, date)
                )
                """
            )
            con.executemany(
                """INSERT OR IGNORE INTO daily_returns
                   (alpha_id, expression_text, date, daily_return, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [(alpha_id, expression_text, date, ret, ts) for date, ret in returns],
            )
            con.commit()
        finally:
            con.close()

    def fetch_daily_returns(self, alpha_id: str) -> list[tuple[str, float]]:
        """Return sorted (date, return) pairs for an alpha, or [] if none stored."""
        if not self.path or not self.path.is_file():
            return []
        con = sqlite3.connect(str(self.path))
        try:
            rows = con.execute(
                "SELECT date, daily_return FROM daily_returns WHERE alpha_id=? ORDER BY date",
                (alpha_id,),
            ).fetchall()
            return [(str(r[0]), float(r[1])) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            con.close()

    def append_row(
        self,
        *,
        utc_iso: str,
        alpha_id: str,
        expression: str,
        status: str,
        queue_status: str,
        sharpe: float | None,
        fitness: float | None,
        turnover: float | None,
        fail_reason: str = "",
    ) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    utc_iso TEXT,
                    alpha_id TEXT,
                    expression TEXT,
                    status TEXT,
                    queue_status TEXT,
                    sharpe REAL,
                    fitness REAL,
                    turnover REAL,
                    fail_reason TEXT
                )
                """
            )
            con.execute(
                """INSERT INTO simulation_runs
                (utc_iso, alpha_id, expression, status, queue_status, sharpe, fitness, turnover, fail_reason)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    utc_iso,
                    alpha_id,
                    expression,
                    status,
                    queue_status,
                    sharpe,
                    fitness,
                    turnover,
                    fail_reason,
                ),
            )
            con.commit()
        finally:
            con.close()
