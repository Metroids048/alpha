"""Idempotent Consultant Factory SQLite migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
    (
        5,
        """
CREATE TABLE IF NOT EXISTS platform_sync_runs (
 sync_id TEXT PRIMARY KEY, filters_json TEXT NOT NULL, declared_count INTEGER NOT NULL,
 fetched_rows INTEGER NOT NULL, unique_alpha_ids INTEGER NOT NULL, duplicate_alpha_ids INTEGER NOT NULL,
 status TEXT NOT NULL, error_message TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_sync_status ON platform_sync_runs(status,completed_at);
CREATE TABLE IF NOT EXISTS platform_alpha_observations (
 sync_id TEXT NOT NULL, alpha_id TEXT NOT NULL, raw_payload_hash TEXT NOT NULL,
 raw_payload_json TEXT NOT NULL, synced_at TEXT NOT NULL,
 PRIMARY KEY(sync_id,alpha_id,raw_payload_hash),
 FOREIGN KEY(sync_id) REFERENCES platform_sync_runs(sync_id)
);
CREATE TABLE IF NOT EXISTS platform_alpha_ledger (
 alpha_id TEXT PRIMARY KEY, sync_id TEXT NOT NULL, platform_status TEXT NOT NULL,
 alpha_type TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, date_created TEXT NOT NULL DEFAULT '',
 date_modified TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', universe_name TEXT NOT NULL DEFAULT '',
 delay TEXT NOT NULL DEFAULT '', expression_hash TEXT NOT NULL, settings_hash TEXT NOT NULL,
 is_metrics_json TEXT NOT NULL DEFAULT '{}', latest_checks_json TEXT NOT NULL DEFAULT '[]',
 regular_description TEXT NOT NULL DEFAULT '', selection_description TEXT NOT NULL DEFAULT '',
 combo_description TEXT NOT NULL DEFAULT '', synced_at TEXT NOT NULL, raw_payload_hash TEXT NOT NULL,
 FOREIGN KEY(sync_id) REFERENCES platform_sync_runs(sync_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_ledger_status ON platform_alpha_ledger(platform_status,alpha_type,hidden);
CREATE TABLE IF NOT EXISTS research_identities (
 identity_id TEXT PRIMARY KEY, economic_mechanism TEXT NOT NULL, information_source TEXT NOT NULL,
 information_timing TEXT NOT NULL, comparison_basis TEXT NOT NULL, field_family TEXT NOT NULL,
 operator_topology TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_trials_vnext (
 trial_id TEXT PRIMARY KEY, identity_id TEXT NOT NULL, alpha_id TEXT NOT NULL DEFAULT '',
 expression_hash TEXT NOT NULL, settings_json TEXT NOT NULL, settings_hash TEXT NOT NULL,
 family TEXT NOT NULL DEFAULT '', dataset TEXT NOT NULL DEFAULT '', platform_sync_id TEXT NOT NULL DEFAULT '',
 metrics_json TEXT NOT NULL DEFAULT '{}', checks_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
 created_at TEXT NOT NULL, FOREIGN KEY(identity_id) REFERENCES research_identities(identity_id)
);
CREATE TABLE IF NOT EXISTS knowledge_sources (
 source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_tier TEXT NOT NULL,
 title TEXT NOT NULL, reference TEXT NOT NULL, published_at TEXT, retrieved_at TEXT NOT NULL,
 evidence_level TEXT NOT NULL, content_hash TEXT NOT NULL, rights_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
 item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, region TEXT NOT NULL DEFAULT '*',
 universe_name TEXT NOT NULL DEFAULT '*', delay TEXT NOT NULL DEFAULT '*', data_category TEXT NOT NULL DEFAULT '',
 economic_mechanism TEXT NOT NULL, settings_json TEXT NOT NULL DEFAULT '{}', risks TEXT NOT NULL DEFAULT '',
 abstract_text TEXT NOT NULL, public_expression_hash TEXT NOT NULL DEFAULT '',
 production_status TEXT NOT NULL DEFAULT 'RESEARCH_ONLY', created_at TEXT NOT NULL,
 FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id)
);
CREATE TABLE IF NOT EXISTS knowledge_validations (
 validation_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, platform_trial_id TEXT NOT NULL,
 status TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 FOREIGN KEY(item_id) REFERENCES knowledge_items(item_id)
);
CREATE TABLE IF NOT EXISTS factory_control (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), hard_stop INTEGER NOT NULL DEFAULT 1,
 reason TEXT NOT NULL, updated_at TEXT NOT NULL, ledger_sync_id TEXT NOT NULL DEFAULT '',
 cluster_freeze_complete INTEGER NOT NULL DEFAULT 0, execute_submit INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO factory_control(singleton,hard_stop,reason,updated_at)
VALUES(1,1,'acceptance_audit_required',CURRENT_TIMESTAMP);
""",
    ),
    (
        6,
        """
CREATE TABLE IF NOT EXISTS platform_sync_pages (
 sync_id TEXT NOT NULL, page_number INTEGER NOT NULL, offset_value INTEGER NOT NULL,
 filters_json TEXT NOT NULL, declared_count INTEGER NOT NULL, result_count INTEGER NOT NULL,
 response_hash TEXT NOT NULL, status TEXT NOT NULL, error_message TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(sync_id,page_number), FOREIGN KEY(sync_id) REFERENCES platform_sync_runs(sync_id)
);
        """,
    ),
    (
        7,
        """
CREATE TABLE IF NOT EXISTS platform_request_events (
 event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, endpoint_class TEXT NOT NULL,
 method TEXT NOT NULL, status_code INTEGER NOT NULL, retry_after_seconds REAL NOT NULL DEFAULT 0,
 retry_after_until TEXT, auth_session_id TEXT NOT NULL, process_id INTEGER NOT NULL,
 request_id TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 1,
 backoff_seconds REAL NOT NULL DEFAULT 0, response_hash TEXT NOT NULL DEFAULT '',
 error_class TEXT NOT NULL DEFAULT '', sync_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_platform_request_events_time ON platform_request_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_platform_request_events_status ON platform_request_events(status_code,endpoint_class);
CREATE TABLE IF NOT EXISTS platform_access_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
 opened_at TEXT, retry_after_until TEXT, recovery_attempts INTEGER NOT NULL DEFAULT 0,
 max_auto_recoveries INTEGER NOT NULL DEFAULT 4, last_successful_auth TEXT,
 last_401 TEXT, last_403 TEXT, last_429 TEXT, last_request_id TEXT NOT NULL DEFAULT '',
 last_session_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO platform_access_state(singleton,state,max_auto_recoveries,updated_at)
VALUES(1,'CLOSED',4,CURRENT_TIMESTAMP);
""",
    ),
    (
        8,
        """
CREATE TABLE IF NOT EXISTS description_schema_observations (
 schema_id TEXT PRIMARY KEY, alpha_type TEXT NOT NULL, source TEXT NOT NULL,
 source_version TEXT NOT NULL DEFAULT '', schema_hash TEXT NOT NULL,
 raw_schema_json TEXT NOT NULL, payload_path_json TEXT NOT NULL,
 min_length INTEGER NOT NULL DEFAULT 0, max_length INTEGER,
 required_sections_json TEXT NOT NULL DEFAULT '[]', observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_description_schema_type
 ON description_schema_observations(alpha_type,observed_at);
CREATE TABLE IF NOT EXISTS alpha_eligibility_snapshots (
 sync_id TEXT NOT NULL, alpha_id TEXT NOT NULL, eligibility_status TEXT NOT NULL,
 reasons_json TEXT NOT NULL DEFAULT '[]', classified_at TEXT NOT NULL,
 PRIMARY KEY(sync_id,alpha_id)
);
CREATE TABLE IF NOT EXISTS platform_write_intents (
 intent_id TEXT PRIMARY KEY, sync_id TEXT NOT NULL, alpha_id TEXT NOT NULL,
 operation TEXT NOT NULL, payload_hash TEXT NOT NULL, expected_version TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
 last_http_status INTEGER, last_error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
 UNIQUE(alpha_id,operation,payload_hash)
);
CREATE TABLE IF NOT EXISTS research_arm_metrics (
 arm_key TEXT PRIMARY KEY, family TEXT NOT NULL, dataset TEXT NOT NULL,
 field_family TEXT NOT NULL, mechanism TEXT NOT NULL, operator_topology TEXT NOT NULL,
 region TEXT NOT NULL, universe_name TEXT NOT NULL, delay TEXT NOT NULL,
 simulation_count INTEGER NOT NULL DEFAULT 0, base_pass_count INTEGER NOT NULL DEFAULT 0,
 near_pass_count INTEGER NOT NULL DEFAULT 0, sharpe_values_json TEXT NOT NULL DEFAULT '[]',
 self_corr_pass_count INTEGER NOT NULL DEFAULT 0, prod_corr_pass_count INTEGER NOT NULL DEFAULT 0,
 final_submit_count INTEGER NOT NULL DEFAULT 0, consecutive_low_windows INTEGER NOT NULL DEFAULT 0,
 sampling_weight REAL NOT NULL DEFAULT 1.0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_freeze_state (
 cluster_id TEXT PRIMARY KEY, explicit_self_fail_count INTEGER NOT NULL DEFAULT 0,
 explicit_self_pass_count INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS description_backfill_jobs (
 job_id TEXT PRIMARY KEY, sync_id TEXT NOT NULL, alpha_id TEXT NOT NULL,
 alpha_type TEXT NOT NULL, eligibility_status TEXT NOT NULL,
 description_status TEXT NOT NULL, description_payload_hash TEXT NOT NULL DEFAULT '',
 platform_before_hash TEXT NOT NULL DEFAULT '', platform_after_hash TEXT NOT NULL DEFAULT '',
 patch_attempt_count INTEGER NOT NULL DEFAULT 0, submit_attempt_count INTEGER NOT NULL DEFAULT 0,
 last_http_status INTEGER, retry_after_until TEXT, last_error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
 job_stage TEXT NOT NULL DEFAULT 'DISCOVERED', schema_hash TEXT NOT NULL DEFAULT '',
 facts_hash TEXT NOT NULL DEFAULT '', expected_version TEXT NOT NULL DEFAULT '',
 patch_intent_id TEXT, submit_intent_id TEXT, uncertain_write INTEGER NOT NULL DEFAULT 0,
 UNIQUE(sync_id,alpha_id)
);
ALTER TABLE platform_alpha_ledger ADD COLUMN description_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE platform_alpha_ledger ADD COLUMN description_schema_hash TEXT NOT NULL DEFAULT '';
""",
    ),
    (
        9,
        """
ALTER TABLE factory_control ADD COLUMN execute_description_patch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE research_identities ADD COLUMN holding_horizon TEXT NOT NULL DEFAULT '';
ALTER TABLE research_identities ADD COLUMN risk_exposure TEXT NOT NULL DEFAULT '';
""",
    ),
    (
        10,
        """
ALTER TABLE description_backfill_jobs ADD COLUMN description_payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE description_backfill_jobs ADD COLUMN description_facts_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE description_backfill_jobs ADD COLUMN validation_errors_json TEXT NOT NULL DEFAULT '[]';
""",
    ),
    (
        11,
        """
ALTER TABLE factory_control ADD COLUMN stop_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE factory_control ADD COLUMN readiness_state TEXT NOT NULL DEFAULT '';
ALTER TABLE factory_control ADD COLUMN readiness_reason TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS loop_health (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), current_cycle INTEGER NOT NULL DEFAULT 0,
 consecutive_cycle_failures INTEGER NOT NULL DEFAULT 0, last_success_at TEXT,
 last_failure_at TEXT, last_failure_category TEXT NOT NULL DEFAULT '',
 last_exception TEXT NOT NULL DEFAULT '', recovery_attempts INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO loop_health(singleton,updated_at) VALUES(1,CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS loop_incidents (
 incident_id INTEGER PRIMARY KEY AUTOINCREMENT, cycle INTEGER NOT NULL, task_id TEXT NOT NULL DEFAULT '',
 input_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL, rc INTEGER NOT NULL,
 consecutive_cycle_failures INTEGER NOT NULL DEFAULT 0, retry_after_seconds REAL,
 detail TEXT NOT NULL DEFAULT '', traceback_text TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loop_incidents_cycle ON loop_incidents(cycle,occurred_at);
CREATE INDEX IF NOT EXISTS idx_loop_incidents_category ON loop_incidents(category,occurred_at);
UPDATE factory_control
SET hard_stop=0, stop_kind='', readiness_state=reason, readiness_reason=reason
WHERE hard_stop=1 AND reason IN (
 'acceptance_audit_required','cluster_freeze_required','acceptance_pilot_pending',
 'ledger_stale','ledger_sync_required','PLATFORM_LEDGER_NOT_COMPLETE'
);
UPDATE factory_control
SET stop_kind='manual'
WHERE hard_stop=1 AND stop_kind='';
UPDATE platform_access_state
SET state='RATE_LIMITED', reason='legacy_rate_limit_recoverable'
WHERE state='MANUAL_INTERVENTION' AND reason IN (
 'max_auto_recoveries_exceeded','manual platform access recovery is required'
);
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
            try:
                connection.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\n"
                    f"INSERT INTO schema_migrations(version) VALUES ({int(version)});\nCOMMIT;"
                )
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
    finally:
        connection.close()


def backup_and_migrate(path: str | Path, backup_path: str | Path | None = None) -> Path | None:
    """Create a verified SQLite backup before applying migrations."""
    target = Path(path)
    backup: Path | None = None
    if target.is_file():
        if backup_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.name}.backup-{stamp}")
        else:
            backup = Path(backup_path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise sqlite3.DatabaseError("backup integrity_check failed")
    migrate(target)
    with sqlite3.connect(target) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise sqlite3.DatabaseError("migrated database integrity_check failed")
    return backup
