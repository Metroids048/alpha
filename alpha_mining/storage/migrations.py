"""Idempotent Consultant Factory SQLite migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
CREATE TABLE IF NOT EXISTS platform_gate_observations (
 observation_id TEXT PRIMARY KEY, gate_name TEXT NOT NULL, result TEXT NOT NULL,
 limit_value REAL, observed_value REAL, message TEXT NOT NULL DEFAULT '', direction TEXT NOT NULL,
 region TEXT NOT NULL DEFAULT '*', universe_name TEXT NOT NULL DEFAULT '*', delay TEXT NOT NULL DEFAULT '*',
 alpha_type TEXT NOT NULL DEFAULT '*', theme_id TEXT NOT NULL DEFAULT '*', pyramid_id TEXT NOT NULL DEFAULT '*',
 source_alpha_id TEXT NOT NULL DEFAULT '', observed_at TEXT, ingested_at TEXT NOT NULL,
 raw_payload_hash TEXT NOT NULL, source TEXT NOT NULL, timestamp_source TEXT NOT NULL,
 freshness_eligible INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gate_observation_lookup ON platform_gate_observations(gate_name,region,universe_name,delay,alpha_type,theme_id,pyramid_id,observed_at);
CREATE TABLE IF NOT EXISTS platform_gate_snapshots (
 snapshot_key TEXT PRIMARY KEY, gate_name TEXT NOT NULL, limit_value REAL NOT NULL, direction TEXT NOT NULL,
 region TEXT NOT NULL, universe_name TEXT NOT NULL, delay TEXT NOT NULL, alpha_type TEXT NOT NULL,
 theme_id TEXT NOT NULL, pyramid_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 observation_count INTEGER NOT NULL, source TEXT NOT NULL, raw_payload_hash TEXT NOT NULL, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_alphas (
 legacy_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, is_canonical INTEGER NOT NULL, exact_hash TEXT NOT NULL,
 normalized_expression TEXT NOT NULL, expression TEXT NOT NULL, alpha_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
 source_row INTEGER NOT NULL, observed_at TEXT, family TEXT NOT NULL DEFAULT '', settings_json TEXT NOT NULL DEFAULT '{}',
 metrics_json TEXT NOT NULL DEFAULT '{}', checks_json TEXT NOT NULL DEFAULT '[]', simulation_json TEXT NOT NULL DEFAULT '{}',
 parse_valid INTEGER NOT NULL DEFAULT 1, imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_legacy_exact_hash ON legacy_alphas(exact_hash);
CREATE TABLE IF NOT EXISTS alpha_check_events (
 event_id TEXT PRIMARY KEY, legacy_id TEXT NOT NULL, name TEXT NOT NULL, result TEXT NOT NULL,
 limit_value REAL, observed_value REAL, raw_json TEXT NOT NULL, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS alpha_expression_features (
 canonical_id TEXT PRIMARY KEY, ast_json TEXT NOT NULL, structure_signature TEXT NOT NULL,
 behavior_signature TEXT NOT NULL, operators_json TEXT NOT NULL, topology TEXT NOT NULL,
 fields_json TEXT NOT NULL, field_categories_json TEXT NOT NULL, windows_json TEXT NOT NULL,
 grouping_json TEXT NOT NULL, normalizers_json TEXT NOT NULL, conditions_json TEXT NOT NULL,
 nesting_depth INTEGER NOT NULL, operator_count INTEGER NOT NULL, unit_warnings_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_features_behavior ON alpha_expression_features(behavior_signature);
CREATE TABLE IF NOT EXISTS alpha_behavior_clusters (
 cluster_id TEXT PRIMARY KEY, behavior_signature TEXT NOT NULL, medoid_legacy_id TEXT,
 member_count INTEGER NOT NULL DEFAULT 0, algorithm TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_cluster_members (
 cluster_id TEXT NOT NULL, legacy_id TEXT NOT NULL, distance REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(cluster_id,legacy_id)
);
CREATE TABLE IF NOT EXISTS alpha_lineage (
 lineage_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, legacy_id TEXT NOT NULL, alpha_id TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL, relationship TEXT NOT NULL, parent_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_trials (
 trial_id TEXT PRIMARY KEY, expression_id TEXT NOT NULL, setting_profile TEXT NOT NULL, parameter_delta_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL, checks_json TEXT NOT NULL, quality_score REAL, robustness_score REAL,
 simulation_cost REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_triage_results (
 legacy_id TEXT PRIMARY KEY, classification TEXT NOT NULL, reason TEXT NOT NULL,
 gate_snapshot_versions_json TEXT NOT NULL DEFAULT '{}', cluster_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_daily_returns (
 expression_id TEXT NOT NULL, alpha_id TEXT NOT NULL DEFAULT '', date TEXT NOT NULL, daily_return REAL NOT NULL,
 source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, PRIMARY KEY(expression_id,date)
);
CREATE TABLE IF NOT EXISTS alpha_correlation_results (
 result_id TEXT PRIMARY KEY, expression_id TEXT NOT NULL, reference_id TEXT NOT NULL, reference_set TEXT NOT NULL,
 overlap INTEGER NOT NULL, pearson REAL, spearman REAL, absolute_correlation REAL,
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consultant_bandit_events (
 event_id TEXT PRIMARY KEY, arm_key TEXT NOT NULL, reward REAL NOT NULL, components_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consultant_submit_queue (
 queue_id TEXT PRIMARY KEY, expression_id TEXT NOT NULL, alpha_id TEXT NOT NULL DEFAULT '', payload_hash TEXT NOT NULL,
 status TEXT NOT NULL, reasons_json TEXT NOT NULL, gate_versions_json TEXT NOT NULL, execute_requested INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
""",
    ),
    (
        2,
        """
ALTER TABLE consultant_submit_queue ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE consultant_submit_queue ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
""",
    ),
    (
        3,
        """
CREATE TABLE IF NOT EXISTS simulation_requests (
 request_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
""",
    ),
    (
        4,
        """
CREATE TABLE IF NOT EXISTS prod_correlation_observations (
 id INTEGER PRIMARY KEY,
 alpha_id TEXT NOT NULL,
 expression_id TEXT NOT NULL DEFAULT '',
 behavior_cluster_id TEXT,
 prod_correlation REAL,
 prod_cutoff REAL,
 required_sharpe_improvement REAL,
 status TEXT NOT NULL,
 failure_message TEXT,
 raw_payload_hash TEXT NOT NULL DEFAULT '',
 observed_at TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'platform_payload',
 UNIQUE(alpha_id, raw_payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_prod_corr_alpha ON prod_correlation_observations(alpha_id);
CREATE INDEX IF NOT EXISTS idx_prod_corr_cluster ON prod_correlation_observations(behavior_cluster_id);
CREATE INDEX IF NOT EXISTS idx_prod_corr_status ON prod_correlation_observations(status, observed_at);
""",
    ),
)


def migrate(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
            )
        connection.commit()
    finally:
        connection.close()
