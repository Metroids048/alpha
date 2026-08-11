"""Idempotent Consultant Factory SQLite migrations."""

from __future__ import annotations

import json
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
    (
        12,
        """
CREATE TABLE IF NOT EXISTS factory_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factory_events_category ON factory_events(category, observed_at);
""",
    ),
    (
        13,
        """
CREATE TABLE IF NOT EXISTS expression_identities (
 expression_id TEXT PRIMARY KEY, exact_hash TEXT NOT NULL, parameter_skeleton TEXT NOT NULL,
 field_skeleton TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(expression_id) REFERENCES expressions(expression_id)
);
CREATE INDEX IF NOT EXISTS idx_expression_identities_exact_hash ON expression_identities(exact_hash);
CREATE INDEX IF NOT EXISTS idx_expression_identities_parameter_skeleton ON expression_identities(parameter_skeleton);
CREATE INDEX IF NOT EXISTS idx_expression_identities_field_skeleton ON expression_identities(field_skeleton);
""",
    ),
    (
        14,
        """
CREATE TABLE IF NOT EXISTS factory_candidate_claims (
 claim_id INTEGER PRIMARY KEY AUTOINCREMENT, expression_text TEXT NOT NULL,
 exact_hash TEXT NOT NULL UNIQUE, parameter_skeleton TEXT NOT NULL UNIQUE,
 field_skeleton TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factory_candidate_claims_status
 ON factory_candidate_claims(status, updated_at);
""",
    ),
    (
        15,
        """
ALTER TABLE factory_candidate_claims RENAME TO factory_candidate_claims_v14;
CREATE TABLE factory_candidate_claims (
 claim_id INTEGER PRIMARY KEY AUTOINCREMENT, expression_text TEXT NOT NULL,
 exact_hash TEXT NOT NULL UNIQUE, parameter_skeleton TEXT NOT NULL,
 field_skeleton TEXT NOT NULL, request_hash TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO factory_candidate_claims
 (claim_id,expression_text,exact_hash,parameter_skeleton,field_skeleton,request_hash,status,created_at,updated_at)
SELECT claim_id,expression_text,exact_hash,parameter_skeleton,field_skeleton,request_hash,status,created_at,updated_at
FROM factory_candidate_claims_v14;
DROP TABLE factory_candidate_claims_v14;
CREATE INDEX idx_factory_candidate_claims_status
 ON factory_candidate_claims(status, updated_at);
CREATE INDEX idx_factory_candidate_claims_parameter_skeleton
 ON factory_candidate_claims(parameter_skeleton);
CREATE INDEX idx_factory_candidate_claims_field_skeleton
 ON factory_candidate_claims(field_skeleton);
        """,
    ),
    (
        16,
        """
CREATE TABLE IF NOT EXISTS simulation_requests (
 request_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE simulation_requests ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE simulation_requests ADD COLUMN lease_started_at TEXT NOT NULL DEFAULT '';
ALTER TABLE simulation_requests ADD COLUMN progress_location TEXT NOT NULL DEFAULT '';
ALTER TABLE simulation_requests ADD COLUMN alpha_id TEXT NOT NULL DEFAULT '';
ALTER TABLE simulation_requests ADD COLUMN last_error TEXT NOT NULL DEFAULT '';
UPDATE simulation_requests
SET status='FAILED',updated_at=CURRENT_TIMESTAMP
WHERE status='CLAIMED' AND EXISTS (
    SELECT 1 FROM factory_candidate_claims fc
    WHERE fc.request_hash=simulation_requests.request_hash AND fc.status='FAILED'
);
UPDATE simulation_requests
SET status='UNKNOWN',last_error='legacy CLAIMED request has no verifiable external checkpoint',updated_at=CURRENT_TIMESTAMP
WHERE status='CLAIMED';
""",
    ),
    (
        17,
        """
ALTER TABLE simulation_requests ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS candidate_outcomes (
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
);
CREATE INDEX IF NOT EXISTS idx_co_topic ON candidate_outcomes(topic_id);
CREATE INDEX IF NOT EXISTS idx_co_family ON candidate_outcomes(strategy_family);
CREATE INDEX IF NOT EXISTS idx_co_skeleton ON candidate_outcomes(field_skeleton);
CREATE TABLE IF NOT EXISTS research_arm_observation_windows (
 arm_key TEXT NOT NULL,
 current_window_count INTEGER NOT NULL DEFAULT 0,
 current_window_base_pass_count INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL,
 PRIMARY KEY (arm_key)
);
        """,
    ),
    (
        18,
        """
ALTER TABLE candidate_outcomes ADD COLUMN quality_status TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN quality_reasons_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE candidate_outcomes ADD COLUMN self_correlation TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN prod_correlation TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN knowledge_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE candidate_outcomes ADD COLUMN parent_candidate_id TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN repair_action TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN operator_topology TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN region TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN universe_name TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN delay TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        19,
        """
ALTER TABLE research_arm_metrics ADD COLUMN arm_state TEXT NOT NULL DEFAULT 'YELLOW';
ALTER TABLE research_arm_observation_windows ADD COLUMN sharpes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN fitnesses_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN base_passes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN near_passes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN self_corr_passes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN prod_corr_passes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_arm_observation_windows ADD COLUMN final_submits_json TEXT NOT NULL DEFAULT '[]';
""",
    ),
    (
        20,
        """
ALTER TABLE candidate_outcomes ADD COLUMN knowledge_usage_mode TEXT NOT NULL DEFAULT 'NONE';
ALTER TABLE candidate_outcomes ADD COLUMN context_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE candidate_outcomes ADD COLUMN knowledge_context_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_outcomes ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0;
""",
    ),
    (
        21,
        """
ALTER TABLE settings_trials ADD COLUMN candidate_id TEXT NOT NULL DEFAULT '';
ALTER TABLE settings_trials ADD COLUMN parent_candidate_id TEXT NOT NULL DEFAULT '';
ALTER TABLE settings_trials ADD COLUMN tune_stage TEXT NOT NULL DEFAULT 'OFAT';
ALTER TABLE settings_trials ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE settings_trials ADD COLUMN request_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE settings_trials ADD COLUMN terminal_status TEXT NOT NULL DEFAULT 'COMPLETE';
ALTER TABLE settings_trials ADD COLUMN outcome TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_settings_trials_budget ON settings_trials(created_at,terminal_status);
CREATE INDEX IF NOT EXISTS idx_settings_trials_lineage ON settings_trials(parent_candidate_id,tune_stage);
""",
    ),
    (
        22,
        """
CREATE TABLE IF NOT EXISTS candidate_work_items (
 candidate_id TEXT PRIMARY KEY,
 request_hash TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL DEFAULT '{}',
 source_evidence_json TEXT NOT NULL DEFAULT '{}',
 state TEXT NOT NULL,
 alpha_id TEXT NOT NULL DEFAULT '',
 metrics_json TEXT NOT NULL DEFAULT '{}',
 checks_json TEXT NOT NULL DEFAULT '[]',
 quality_reasons_json TEXT NOT NULL DEFAULT '[]',
 description_status TEXT NOT NULL DEFAULT 'NOT_PREPARED',
 submission_status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED',
 parent_candidate_id TEXT NOT NULL DEFAULT '',
 tune_child_count INTEGER NOT NULL DEFAULT 0,
 last_error_category TEXT NOT NULL DEFAULT '',
 last_error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(request_hash)
);
CREATE INDEX IF NOT EXISTS idx_candidate_work_items_state ON candidate_work_items(state,updated_at);
CREATE INDEX IF NOT EXISTS idx_candidate_work_items_alpha ON candidate_work_items(alpha_id);
CREATE TABLE IF NOT EXISTS candidate_work_events (
 event_id TEXT PRIMARY KEY,
 candidate_id TEXT NOT NULL,
 event_at TEXT NOT NULL,
 event_type TEXT NOT NULL,
 old_state TEXT NOT NULL DEFAULT '',
 new_state TEXT NOT NULL DEFAULT '',
 details_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(candidate_id) REFERENCES candidate_work_items(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_candidate_work_events_item ON candidate_work_events(candidate_id,event_at);
CREATE TABLE IF NOT EXISTS candidate_batch_intents (
 batch_id TEXT PRIMARY KEY,
 candidate_ids_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL,
 confirmed_at TEXT,
 last_error TEXT NOT NULL DEFAULT ''
);
""",
    ),
    (
        23,
        """
ALTER TABLE candidate_outcomes ADD COLUMN provenance TEXT NOT NULL DEFAULT 'UNVERIFIED';
UPDATE candidate_outcomes SET provenance='PLATFORM_VERIFIED'
 WHERE TRIM(COALESCE(checks_json,'')) NOT IN ('','[]','null');
UPDATE candidate_outcomes SET provenance='PLATFORM_ERROR'
 WHERE provenance<>'PLATFORM_VERIFIED' AND TRIM(COALESCE(error_category,''))<>'';
UPDATE candidate_outcomes SET provenance='SYNTHETIC_PRIOR'
 WHERE provenance NOT IN ('PLATFORM_VERIFIED','PLATFORM_ERROR');
CREATE INDEX IF NOT EXISTS idx_co_provenance ON candidate_outcomes(provenance,outcome);
""",
    ),
    (
        24,
        """
CREATE TABLE IF NOT EXISTS recovery_runs (
 run_id TEXT PRIMARY KEY,
 started_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 status TEXT NOT NULL,
 target_qualified INTEGER NOT NULL DEFAULT 3,
 history_fingerprint TEXT NOT NULL DEFAULT '',
 policy_json TEXT NOT NULL DEFAULT '{}',
 blocker_json TEXT NOT NULL DEFAULT '{}',
 total_real_simulations INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recovery_historical_index (
 history_id TEXT PRIMARY KEY,
 source_name TEXT NOT NULL,
 source_ref TEXT NOT NULL DEFAULT '',
 alpha_id TEXT NOT NULL DEFAULT '',
 expression TEXT NOT NULL DEFAULT '',
 exact_hash TEXT NOT NULL DEFAULT '',
 parameter_skeleton TEXT NOT NULL DEFAULT '',
 field_skeleton TEXT NOT NULL DEFAULT '',
 dataset TEXT NOT NULL DEFAULT '',
 field_family TEXT NOT NULL DEFAULT '',
 operator_topology TEXT NOT NULL DEFAULT '',
 features_json TEXT NOT NULL DEFAULT '{}',
 settings_json TEXT NOT NULL DEFAULT '{}',
 metrics_json TEXT NOT NULL DEFAULT '{}',
 checks_json TEXT NOT NULL DEFAULT '[]',
 evidence_class TEXT NOT NULL DEFAULT 'LOCAL_ONLY',
 self_correlation_status TEXT NOT NULL DEFAULT '',
 self_correlation_value REAL,
 observed_at TEXT NOT NULL DEFAULT '',
 source_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_recovery_history_evidence ON recovery_historical_index(evidence_class,dataset,field_family);
CREATE INDEX IF NOT EXISTS idx_recovery_history_hash ON recovery_historical_index(exact_hash,parameter_skeleton);
CREATE TABLE IF NOT EXISTS recovery_candidates (
 candidate_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 expression TEXT NOT NULL,
 exact_hash TEXT NOT NULL,
 parameter_skeleton TEXT NOT NULL,
 field_skeleton TEXT NOT NULL,
 search_arm TEXT NOT NULL,
 parent_candidate_id TEXT NOT NULL DEFAULT '',
 parent_history_id TEXT NOT NULL DEFAULT '',
 lineage_json TEXT NOT NULL DEFAULT '{}',
 dataset TEXT NOT NULL DEFAULT '',
 field_family TEXT NOT NULL DEFAULT '',
 operator_topology TEXT NOT NULL DEFAULT '',
 settings_json TEXT NOT NULL DEFAULT '{}',
 state TEXT NOT NULL,
 alpha_id TEXT NOT NULL DEFAULT '',
 metrics_json TEXT NOT NULL DEFAULT '{}',
 checks_json TEXT NOT NULL DEFAULT '[]',
 self_correlation_status TEXT NOT NULL DEFAULT '',
 self_correlation_value REAL,
 request_hash TEXT NOT NULL DEFAULT '',
 error_category TEXT NOT NULL DEFAULT '',
 error_message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(run_id,exact_hash),
 FOREIGN KEY(run_id) REFERENCES recovery_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_recovery_candidates_run_state ON recovery_candidates(run_id,state,updated_at);
CREATE INDEX IF NOT EXISTS idx_recovery_candidates_arm ON recovery_candidates(run_id,search_arm,state);
CREATE TABLE IF NOT EXISTS recovery_arm_windows (
 window_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 batch_number INTEGER NOT NULL,
 search_arm TEXT NOT NULL,
 allocation INTEGER NOT NULL,
 statistics_json TEXT NOT NULL DEFAULT '{}',
 improved INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 UNIQUE(run_id,batch_number,search_arm),
 FOREIGN KEY(run_id) REFERENCES recovery_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_recovery_arm_windows_run ON recovery_arm_windows(run_id,batch_number,search_arm);
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
            if version == 19:
                _ensure_research_arm_prerequisites(connection)
            if version == 21:
                _ensure_settings_trials_prerequisites(connection)
            try:
                connection.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\n"
                    f"INSERT INTO schema_migrations(version) VALUES ({int(version)});\nCOMMIT;"
                )
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "simulation_requests",
            "simulation_runs",
            "factory_candidate_claims",
        }
        if required <= tables:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT request_hash,payload_json FROM simulation_requests
                   WHERE status='UNKNOWN'
                     AND last_error='legacy CLAIMED request has no verifiable external checkpoint'"""
            ).fetchall()
            for request_hash, payload_json in rows:
                try:
                    expression = str(
                        json.loads(str(payload_json)).get("regular") or ""
                    ).strip()
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not expression:
                    continue
                run = connection.execute(
                    """SELECT alpha_id FROM simulation_runs
                       WHERE expression=? AND TRIM(COALESCE(alpha_id,''))<>''
                         AND UPPER(COALESCE(status,'')) NOT IN ('FAILED','ERROR','REJECTED')
                       ORDER BY id DESC LIMIT 1""",
                    (expression,),
                ).fetchone()
                if not run:
                    continue
                connection.execute(
                    """UPDATE simulation_requests
                       SET status='COMPLETE',alpha_id=?,last_error='',updated_at=?
                       WHERE request_hash=? AND status='UNKNOWN'""",
                    (str(run[0]).strip(), now, request_hash),
                )
                connection.execute(
                    """UPDATE factory_candidate_claims
                       SET status='SIMULATED',updated_at=? WHERE request_hash=?""",
                    (now, request_hash),
                )
            connection.commit()
    finally:
        connection.close()


def _ensure_research_arm_prerequisites(connection: sqlite3.Connection) -> None:
    """Recover legacy DBs that claim migration 8 but lack its tables."""

    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "research_arm_metrics" not in tables:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS research_arm_metrics (
               arm_key TEXT PRIMARY KEY, family TEXT NOT NULL, dataset TEXT NOT NULL,
               field_family TEXT NOT NULL, mechanism TEXT NOT NULL, operator_topology TEXT NOT NULL,
               region TEXT NOT NULL, universe_name TEXT NOT NULL, delay TEXT NOT NULL,
               simulation_count INTEGER NOT NULL DEFAULT 0, base_pass_count INTEGER NOT NULL DEFAULT 0,
               near_pass_count INTEGER NOT NULL DEFAULT 0, sharpe_values_json TEXT NOT NULL DEFAULT '[]',
               self_corr_pass_count INTEGER NOT NULL DEFAULT 0, prod_corr_pass_count INTEGER NOT NULL DEFAULT 0,
               final_submit_count INTEGER NOT NULL DEFAULT 0, consecutive_low_windows INTEGER NOT NULL DEFAULT 0,
               sampling_weight REAL NOT NULL DEFAULT 1.0, updated_at TEXT NOT NULL
            )"""
        )
    if "research_arm_observation_windows" not in tables:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS research_arm_observation_windows (
               arm_key TEXT NOT NULL PRIMARY KEY,
               current_window_count INTEGER NOT NULL DEFAULT 0,
               current_window_base_pass_count INTEGER NOT NULL DEFAULT 0,
               updated_at TEXT NOT NULL
            )"""
        )


def _ensure_settings_trials_prerequisites(connection: sqlite3.Connection) -> None:
    """Recover sparse legacy databases which recorded migration 1 without its table."""

    connection.execute(
        """CREATE TABLE IF NOT EXISTS settings_trials (
           trial_id TEXT PRIMARY KEY, expression_id TEXT NOT NULL, setting_profile TEXT NOT NULL,
           parameter_delta_json TEXT NOT NULL, metrics_json TEXT NOT NULL, checks_json TEXT NOT NULL,
           quality_score REAL, robustness_score REAL, simulation_cost REAL NOT NULL DEFAULT 0,
           created_at TEXT NOT NULL
        )"""
    )
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
