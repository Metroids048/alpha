from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _ready_alpha(**overrides):
    row = {
        "alpha_id": "alpha-1",
        "platform_status": "UNSUBMITTED",
        "checks_fresh": True,
        "checks": [
            {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
            {"name": "LOW_FITNESS", "result": "PASS", "mandatory": True},
            {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
            {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
        ],
        "description_required": True,
        "description_valid": False,
        "schema_known": True,
        "submission_pending": False,
    }
    row.update(overrides)
    return row


def test_eligibility_only_description_missing_is_backfillable() -> None:
    from alpha_mining.description.eligibility import EligibilityStatus, classify_alpha

    assert classify_alpha(_ready_alpha()).status is EligibilityStatus.SUBMIT_READY_EXCEPT_DESCRIPTION


def test_description_check_failure_is_the_only_automatic_backfill_exception() -> None:
    from alpha_mining.description.eligibility import EligibilityStatus, classify_alpha

    row = _ready_alpha()
    row["checks"] = [*row["checks"], {"name": "DESCRIPTION", "result": "FAIL"}]
    assert classify_alpha(row).status is EligibilityStatus.SUBMIT_READY_EXCEPT_DESCRIPTION


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"platform_status": "SUBMITTED"}, "ALREADY_SUBMITTED"),
        ({"submission_pending": True}, "SUBMISSION_PENDING"),
        ({"checks_fresh": False}, "STALE_CHECKS"),
        ({"checks": [{"name": "LOW_SHARPE", "result": "FAIL", "mandatory": True}]}, "BASE_GATE_FAILED"),
        ({"checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "mandatory": True}]}, "SELF_CORR_FAILED"),
        ({"checks": [{"name": "PROD_CORRELATION", "result": "FAIL", "mandatory": True}]}, "PROD_CORR_FAILED"),
        ({"schema_known": False}, "DESCRIPTION_SCHEMA_UNKNOWN"),
        ({"description_valid": True}, "SUBMIT_READY"),
        ({"checks": [{"name": "SELF_CORRELATION", "result": "UNKNOWN", "mandatory": True}]}, "UNKNOWN_BLOCKED"),
    ],
)
def test_eligibility_precedence_is_fail_closed(overrides: dict, expected: str) -> None:
    from alpha_mining.description.eligibility import classify_alpha

    assert classify_alpha(_ready_alpha(**overrides)).status.value == expected


def test_schema_registry_uses_discovered_payload_path_for_dynamic_type(tmp_path: Path) -> None:
    from alpha_mining.description.schema import DescriptionSchemaRegistry
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "schema.sqlite"
    migrate(database)
    registry = DescriptionSchemaRegistry(database)
    resolved = registry.observe(
        alpha_type="NEXT_GEN",
        source="alpha_metadata",
        raw_schema={
            "payloadPath": ["nextGen", "narrative"],
            "type": "string",
            "minLength": 80,
            "maxLength": 1200,
            "requiredSections": ["hypothesis", "risks"],
        },
        source_version="v42",
    )

    assert resolved.alpha_type == "NEXT_GEN"
    assert resolved.payload_path == ("nextGen", "narrative")
    assert registry.resolve("NEXT_GEN") == resolved
    assert registry.resolve("UNKNOWN") is None


def test_description_state_machine_rejects_skipping_validation() -> None:
    from alpha_mining.description.models import DescriptionStatus, transition_description

    assert transition_description(DescriptionStatus.REQUIRED, DescriptionStatus.GENERATED) is DescriptionStatus.GENERATED
    with pytest.raises(ValueError, match="invalid description transition"):
        transition_description(DescriptionStatus.GENERATED, DescriptionStatus.PATCH_PENDING)


def test_phase1_migration_creates_idempotent_description_tables(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "phase1.sqlite"
    migrate(database)
    migrate(database)
    with sqlite3.connect(database) as con:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(description_backfill_jobs)")
        }
        indexes = {
            row[1]
            for row in con.execute("PRAGMA index_list(description_backfill_jobs)")
            if row[2]
        }

    assert {
        "description_schema_observations",
        "alpha_eligibility_snapshots",
        "platform_write_intents",
        "research_arm_metrics",
        "cluster_freeze_state",
        "description_backfill_jobs",
    } <= tables
    assert {
        "job_id",
        "sync_id",
        "alpha_id",
        "alpha_type",
        "eligibility_status",
        "description_status",
        "description_payload_hash",
        "platform_before_hash",
        "platform_after_hash",
        "patch_attempt_count",
        "submit_attempt_count",
        "last_http_status",
        "retry_after_until",
        "last_error",
        "created_at",
        "updated_at",
        "completed_at",
        "job_stage",
        "schema_hash",
        "facts_hash",
        "expected_version",
        "patch_intent_id",
        "submit_intent_id",
        "uncertain_write",
    } <= columns
    assert indexes


def _schema(tmp_path: Path, *, alpha_type: str = "REGULAR"):
    from alpha_mining.description.schema import DescriptionSchemaRegistry
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "description.sqlite"
    migrate(database)
    schema = DescriptionSchemaRegistry(database).observe(
        alpha_type=alpha_type,
        source="fixture",
        raw_schema={
            "payloadPath": ["description", "text"],
            "minLength": 100,
            "maxLength": 4000,
            "requiredSections": [
                "hypothesis",
                "data_rationale",
                "signal_construction",
                "long_short_interpretation",
                "settings_rationale",
                "risks_and_limitations",
            ],
        },
    )
    return database, schema


def test_facts_are_extracted_from_ast_metadata_and_settings(tmp_path: Path) -> None:
    from alpha_mining.description.facts import extract_description_facts

    facts = extract_description_facts(
        alpha_type="REGULAR",
        expression="group_rank(ts_delta(close, 21), subindustry)",
        field_metadata={"close": {"description": "closing price", "dataset": "pv"}},
        operator_definitions={
            "group_rank": "rank within a group",
            "ts_delta": "change over a time window",
        },
        hypothesis={"mechanism": "price change", "expected_direction": "higher_is_long"},
        settings={
            "delay": 1,
            "neutralization": "SUBINDUSTRY",
            "decay": 2,
            "truncation": 0.08,
            "nanHandling": "ON",
            "pasteurization": "ON",
        },
    )

    assert facts.ast["kind"] == "call"
    assert facts.fields == ("close",)
    assert facts.operators == ("group_rank", "ts_delta")
    assert facts.windows == (21,)
    assert facts.groups == ("subindustry",)
    assert facts.direction == "higher_is_long"
    assert facts.settings["delay"] == 1


def test_unknown_expression_field_fails_before_generation() -> None:
    from alpha_mining.description.facts import DescriptionFactError, extract_description_facts

    with pytest.raises(DescriptionFactError, match="unsupported fields"):
        extract_description_facts(
            alpha_type="REGULAR",
            expression="rank(not_a_real_field)",
            field_metadata={},
            operator_definitions={"rank": "cross-sectional rank"},
            hypothesis={"mechanism": "test", "expected_direction": "higher_is_long"},
            settings={"delay": 1},
        )


def test_deterministic_description_builds_schema_payload_and_validates(tmp_path: Path) -> None:
    from alpha_mining.description.engine import build_deterministic_description
    from alpha_mining.description.facts import extract_description_facts
    from alpha_mining.description.validator import validate_description

    _, schema = _schema(tmp_path)
    facts = extract_description_facts(
        alpha_type="REGULAR",
        expression="group_rank(ts_delta(close, 21), subindustry)",
        field_metadata={"close": {"description": "daily closing price", "dataset": "pv"}},
        operator_definitions={"group_rank": "peer ranking", "ts_delta": "time change"},
        hypothesis={"mechanism": "medium-horizon price change", "expected_direction": "higher_is_long"},
        settings={"delay": 1, "neutralization": "SUBINDUSTRY", "decay": 2, "truncation": 0.08, "nanHandling": "ON", "pasteurization": "ON"},
    )
    draft = build_deterministic_description(facts, schema)
    validation = validate_description(draft, facts, schema)

    assert validation.valid
    assert draft.payload["description"]["text"] == draft.text
    assert set(schema.required_sections) <= set(draft.sections)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("Sharpe is 2.5 and returns are strong.", "UNSUPPORTED_PERFORMANCE_CLAIM"),
        ("TODO replace this placeholder.", "PLACEHOLDER_DETECTED"),
        ("This is a good alpha signal.", "GENERIC_DESCRIPTION"),
    ],
)
def test_validator_rejects_hallucination_placeholder_and_generic_text(
    tmp_path: Path, replacement: str, expected: str
) -> None:
    from dataclasses import replace
    from alpha_mining.description.engine import build_deterministic_description
    from alpha_mining.description.facts import extract_description_facts
    from alpha_mining.description.validator import validate_description

    _, schema = _schema(tmp_path)
    facts = extract_description_facts(
        alpha_type="REGULAR",
        expression="rank(close)",
        field_metadata={"close": {"description": "closing price"}},
        operator_definitions={"rank": "cross-sectional rank"},
        hypothesis={"mechanism": "relative price level", "expected_direction": "higher_is_long"},
        settings={"delay": 1},
    )
    draft = build_deterministic_description(facts, schema)
    invalid = replace(draft, text=replacement, payload={"description": {"text": replacement}})

    assert expected in validate_description(invalid, facts, schema).errors


def test_backfill_job_is_created_only_for_description_only_candidate(tmp_path: Path) -> None:
    from alpha_mining.description.jobs import DescriptionJobStore
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "jobs.sqlite"
    migrate(database)
    store = DescriptionJobStore(database)
    first = store.ensure_job(sync_id="sync-1", alpha=_ready_alpha())
    second = store.ensure_job(sync_id="sync-1", alpha=_ready_alpha())
    blocked = store.ensure_job(
        sync_id="sync-1",
        alpha=_ready_alpha(checks=[{"name": "LOW_SHARPE", "result": "FAIL"}]),
    )

    assert first is not None and second is not None
    assert first.job_id == second.job_id
    assert blocked is None
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM description_backfill_jobs").fetchone()[0] == 1


class _PatchGateway:
    def __init__(self, *, timeout: bool = False, apply_before_timeout: bool = False) -> None:
        self.timeout = timeout
        self.apply_before_timeout = apply_before_timeout
        self.calls: list[str] = []
        self.alpha = {
            "id": "alpha-1",
            "status": "UNSUBMITTED",
            "version": "1",
            "description": {"text": "old"},
        }

    def fetch_alpha(self, alpha_id: str) -> dict:
        self.calls.append("GET")
        return json.loads(json.dumps(self.alpha))

    def patch_alpha(self, alpha_id: str, payload: dict) -> dict:
        self.calls.append("PATCH")
        if self.apply_before_timeout:
            self.alpha["description"] = payload["description"]
            self.alpha["version"] = "2"
        if self.timeout:
            raise TimeoutError("ambiguous timeout")
        self.alpha["description"] = payload["description"]
        self.alpha["version"] = "2"
        return {"status_code": 200}


def test_patch_success_requires_get_readback(tmp_path: Path) -> None:
    from alpha_mining.description.delivery import DescriptionDelivery
    from alpha_mining.description.models import DescriptionStatus

    database, _ = _schema(tmp_path)
    gateway = _PatchGateway()
    result = DescriptionDelivery(database, gateway).patch_once(
        sync_id="sync-1",
        alpha_id="alpha-1",
        alpha_type="REGULAR",
        payload={"description": {"text": "new description"}},
        payload_path=("description", "text"),
        execute=True,
    )

    assert result.status is DescriptionStatus.VERIFIED
    assert gateway.calls == ["GET", "PATCH", "GET"]


def test_patch_timeout_gets_before_any_replay_and_can_reconcile(tmp_path: Path) -> None:
    from alpha_mining.description.delivery import DescriptionDelivery
    from alpha_mining.description.models import DescriptionStatus

    database, _ = _schema(tmp_path)
    gateway = _PatchGateway(timeout=True, apply_before_timeout=True)
    result = DescriptionDelivery(database, gateway).patch_once(
        sync_id="sync-1",
        alpha_id="alpha-1",
        alpha_type="REGULAR",
        payload={"description": {"text": "new description"}},
        payload_path=("description", "text"),
        execute=True,
    )

    assert result.status is DescriptionStatus.VERIFIED
    assert gateway.calls == ["GET", "PATCH", "GET"]


def test_patch_timeout_without_platform_change_stays_uncertain(tmp_path: Path) -> None:
    from alpha_mining.description.delivery import DescriptionDelivery
    from alpha_mining.description.models import DescriptionStatus

    database, _ = _schema(tmp_path)
    gateway = _PatchGateway(timeout=True, apply_before_timeout=False)
    result = DescriptionDelivery(database, gateway).patch_once(
        sync_id="sync-1",
        alpha_id="alpha-1",
        alpha_type="REGULAR",
        payload={"description": {"text": "new description"}},
        payload_path=("description", "text"),
        execute=True,
    )

    assert result.status is DescriptionStatus.PATCH_PENDING
    assert result.uncertain
    assert gateway.calls == ["GET", "PATCH", "GET"]


def test_submission_guard_requires_prod_description_status_and_clean_write_state() -> None:
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    context = CandidateContext(
        alpha_id="alpha-1",
        expression_id="expr-1",
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
            {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
        ],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="PASS",
        ledger_status="COMPLETE",
        ledger_synced_at="2999-01-01T00:00:00Z",
        ledger_sync_id="sync-1",
        candidate_sync_id="sync-1",
        platform_status="UNSUBMITTED",
        description_status="VALIDATED",
        prod_correlation_required=True,
        write_intent_statuses=("UNCERTAIN",),
        execute_submit_enabled=False,
    )
    decision = SubmissionGuard().evaluate(context)

    assert not decision.allowed
    assert "PROD_CORRELATION_MISSING" in decision.reasons
    assert "DESCRIPTION_NOT_VERIFIED" in decision.reasons
    assert "WRITE_INTENT_UNCERTAIN" in decision.reasons
    assert "EXECUTE_SUBMIT_DISABLED" in decision.reasons


class _SubmitGateway:
    def __init__(self, *, timeout: bool, apply_before_timeout: bool) -> None:
        self.timeout = timeout
        self.apply_before_timeout = apply_before_timeout
        self.calls: list[str] = []
        self.status = "UNSUBMITTED"

    def fetch_alpha(self, alpha_id: str) -> dict:
        self.calls.append("GET")
        return {"id": alpha_id, "status": self.status, "version": "1"}

    def submit_alpha(self, alpha_id: str) -> dict:
        self.calls.append("SUBMIT")
        if self.apply_before_timeout:
            self.status = "SUBMITTED"
        if self.timeout:
            raise TimeoutError("ambiguous submit")
        self.status = "SUBMITTED"
        return {"status_code": 201}


def test_submit_timeout_gets_before_replay_and_reconciles(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.submitter.delivery import SubmissionDelivery, SubmissionStatus

    database = tmp_path / "submit.sqlite"
    migrate(database)
    gateway = _SubmitGateway(timeout=True, apply_before_timeout=True)
    result = SubmissionDelivery(database, gateway).submit_once(
        sync_id="sync-1", alpha_id="alpha-1", execute=True
    )

    assert result.status is SubmissionStatus.VERIFIED
    assert gateway.calls == ["GET", "SUBMIT", "GET"]


def test_submit_timeout_without_state_change_is_uncertain(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.submitter.delivery import SubmissionDelivery, SubmissionStatus

    database = tmp_path / "submit.sqlite"
    migrate(database)
    gateway = _SubmitGateway(timeout=True, apply_before_timeout=False)
    result = SubmissionDelivery(database, gateway).submit_once(
        sync_id="sync-1", alpha_id="alpha-1", execute=True
    )

    assert result.status is SubmissionStatus.UNCERTAIN
    assert gateway.calls == ["GET", "SUBMIT", "GET"]


def test_schema_discovery_fails_closed_without_platform_evidence(tmp_path: Path) -> None:
    from alpha_mining.description.schema import DescriptionSchemaRegistry
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "schema-discovery.sqlite"
    migrate(database)
    registry = DescriptionSchemaRegistry(database)

    assert registry.observe_from_payload(
        alpha_type="REGULAR", source="alpha_metadata", payload={"type": "REGULAR"}
    ) is None
    discovered = registry.observe_from_payload(
        alpha_type="SELECTION",
        source="validation_error",
        payload={
            "details": {
                "requiredDescriptionSchema": {
                    "payloadPath": ["selection", "description"],
                    "minLength": 120,
                }
            }
        },
    )
    assert discovered is not None
    assert discovered.payload_path == ("selection", "description")


def test_description_pipeline_prepares_validated_job_without_platform_write(tmp_path: Path) -> None:
    from alpha_mining.description.pipeline import DescriptionPipeline
    from alpha_mining.description.schema import DescriptionSchemaRegistry
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "pipeline.sqlite"
    migrate(database)
    DescriptionSchemaRegistry(database).observe(
        alpha_type="REGULAR",
        source="fixture",
        raw_schema={
            "payloadPath": ["description", "text"],
            "minLength": 100,
            "maxLength": 4000,
            "requiredSections": [
                "hypothesis",
                "data_rationale",
                "signal_construction",
                "long_short_interpretation",
                "settings_rationale",
                "risks_and_limitations",
            ],
        },
    )
    prepared = DescriptionPipeline(database).prepare(
        sync_id="sync-1",
        alpha={**_ready_alpha(), "alpha_type": "REGULAR"},
        expression="rank(close)",
        field_metadata={"close": {"description": "closing price"}},
        operator_definitions={"rank": "cross-sectional rank"},
        hypothesis={"mechanism": "relative price level", "expected_direction": "higher_is_long"},
        settings={"delay": 1, "neutralization": "SUBINDUSTRY"},
    )

    assert prepared is not None
    assert prepared.validation.valid
    assert prepared.draft.status.value == "GENERATED"
    with sqlite3.connect(database) as con:
        row = con.execute(
            "SELECT description_status,patch_attempt_count,submit_attempt_count FROM description_backfill_jobs"
        ).fetchone()
    assert row == ("VALIDATED", 0, 0)


def test_description_pipeline_does_not_prepare_base_failure(tmp_path: Path) -> None:
    from alpha_mining.description.pipeline import DescriptionPipeline
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "blocked.sqlite"
    migrate(database)
    prepared = DescriptionPipeline(database).prepare(
        sync_id="sync-1",
        alpha=_ready_alpha(checks=[{"name": "LOW_SHARPE", "result": "FAIL"}]),
        expression="rank(close)",
        field_metadata={"close": {"description": "closing price"}},
        operator_definitions={"rank": "rank"},
        hypothesis={"mechanism": "price", "expected_direction": "higher_is_long"},
        settings={"delay": 1},
    )
    assert prepared is None
