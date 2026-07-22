"""Regression coverage for the Phase 1 description audit deliverables."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


PHASE1_ARTIFACTS = (
    "DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md",
    "DESCRIPTION_PIPELINE_IMPLEMENTATION.md",
    "ALPHA_QUALITY_CORRELATION_FIX.md",
    "description_backfill_dry_run.csv",
    "description_validation_failures.csv",
    "historical_alpha_eligibility.csv",
    "new_alpha_failure_funnel.csv",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _seed_current_description_evidence(
    database: Path,
    *,
    declared_count: int = 1,
    fetched_rows: int = 1,
    unique_alpha_ids: int = 1,
    duplicate_alpha_ids: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO platform_sync_runs
            (sync_id,filters_json,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,
             status,error_message,started_at,completed_at)
            VALUES ('sync-current','{}',?,?,?,?, 'COMPLETE','',?,?)""",
            (
                declared_count,
                fetched_rows,
                unique_alpha_ids,
                duplicate_alpha_ids,
                now,
                now,
            ),
        )
        con.execute(
            """INSERT INTO platform_alpha_ledger
            (alpha_id,sync_id,platform_status,alpha_type,hidden,expression_hash,settings_hash,
             latest_checks_json,synced_at,raw_payload_hash)
            VALUES ('ready-alpha','sync-current','UNSUBMITTED','REGULAR',0,'expr','settings',
                    '[{"name":"LOW_SHARPE","result":"PASS"}]',?,'payload')""",
            (now,),
        )
        con.execute(
            """INSERT INTO alpha_eligibility_snapshots
            (sync_id,alpha_id,eligibility_status,reasons_json,classified_at)
            VALUES ('sync-current','ready-alpha','SUBMIT_READY_EXCEPT_DESCRIPTION',
                    '["DESCRIPTION_REQUIRED"]',?)""",
            (now,),
        )
        for alpha_id, description_status, errors in (
            ("ready-alpha", "VALIDATED", "[]"),
            ("failed-alpha", "FAILED", '["VALIDATION_FAILED"]'),
            ("unknown-alpha", "SCHEMA_UNKNOWN", '["DESCRIPTION_SCHEMA_UNKNOWN"]'),
        ):
            con.execute(
                """INSERT INTO description_backfill_jobs
                (job_id,sync_id,alpha_id,alpha_type,eligibility_status,description_status,
                 validation_errors_json,last_error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'',?,?)""",
                (
                    f"job-{alpha_id}",
                    "sync-current",
                    alpha_id,
                    "REGULAR",
                    "SUBMIT_READY_EXCEPT_DESCRIPTION",
                    description_status,
                    errors,
                    now,
                    now,
                ),
            )


def _assert_phase1_ledger_blocked(database: Path, output: Path) -> None:
    from alpha_mining.audit.acceptance import run_acceptance_audit

    result = run_acceptance_audit(database, output)

    assert result.status == "BLOCKED"
    assert "PLATFORM_LEDGER_NOT_COMPLETE" in result.blockers
    backfill = _read_csv(output / "description_backfill_dry_run.csv")
    assert backfill[0]["status"] == "BLOCKED"
    assert backfill[0]["reason"] == "PLATFORM_LEDGER_NOT_COMPLETE"
    eligibility = _read_csv(output / "historical_alpha_eligibility.csv")
    assert eligibility[0]["eligibility_status"] == "BLOCKED"
    assert eligibility[0]["reasons"] == "PLATFORM_LEDGER_NOT_COMPLETE"


@pytest.mark.parametrize(
    ("declared_count", "fetched_rows", "unique_alpha_ids"),
    ((2, 1, 1), (1, 2, 1), (1, 1, 2)),
)
def test_phase1_rejects_declared_fetched_or_unique_count_mismatch(
    tmp_path: Path,
    declared_count: int,
    fetched_rows: int,
    unique_alpha_ids: int,
) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "count-mismatch.sqlite"
    migrate(database)
    _seed_current_description_evidence(
        database,
        declared_count=declared_count,
        fetched_rows=fetched_rows,
        unique_alpha_ids=unique_alpha_ids,
    )

    _assert_phase1_ledger_blocked(database, tmp_path)


def test_phase1_rejects_sync_with_duplicate_alpha_ids(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "duplicates.sqlite"
    migrate(database)
    _seed_current_description_evidence(database, duplicate_alpha_ids=1)

    _assert_phase1_ledger_blocked(database, tmp_path)


def test_phase1_rejects_actual_ledger_row_count_mismatch(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "ledger-row-mismatch.sqlite"
    migrate(database)
    _seed_current_description_evidence(
        database,
        declared_count=2,
        fetched_rows=2,
        unique_alpha_ids=2,
    )

    _assert_phase1_ledger_blocked(database, tmp_path)


def test_phase1_artifacts_fail_closed_without_a_fresh_nonzero_ledger(tmp_path: Path) -> None:
    from alpha_mining.audit.acceptance import run_acceptance_audit
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "blocked.sqlite"
    migrate(database)

    result = run_acceptance_audit(database, tmp_path)

    assert result.status == "BLOCKED"
    assert all((tmp_path / name).is_file() for name in PHASE1_ARTIFACTS)
    for name in PHASE1_ARTIFACTS[:3]:
        report = (tmp_path / name).read_text(encoding="utf-8")
        assert "BLOCKED" in report
        assert "PLATFORM_LEDGER_NOT_COMPLETE" in report
        assert "PATCH endpoint calls: 0" in report
        assert "Submit endpoint calls: 0" in report
    assert _read_csv(tmp_path / "description_backfill_dry_run.csv") == [
        {
            "alpha_id": "",
            "sync_id": "",
            "alpha_type": "",
            "eligibility_status": "",
            "description_status": "",
            "status": "BLOCKED",
            "reason": "PLATFORM_LEDGER_NOT_COMPLETE",
            "endpoint_calls": "0",
        }
    ]
    assert _read_csv(tmp_path / "historical_alpha_eligibility.csv") == [
        {
            "alpha_id": "",
            "sync_id": "",
            "eligibility_status": "BLOCKED",
            "reasons": "PLATFORM_LEDGER_NOT_COMPLETE",
            "source": "AUTHORITATIVE_PLATFORM_LEDGER",
        }
    ]


def test_phase1_artifacts_use_current_ledger_jobs_and_persisted_failures(tmp_path: Path) -> None:
    from alpha_mining.audit.acceptance import run_acceptance_audit
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "current.sqlite"
    migrate(database)
    _seed_current_description_evidence(database)

    result = run_acceptance_audit(database, tmp_path)

    assert result.status == "PASS"
    assert all((tmp_path / name).is_file() for name in PHASE1_ARTIFACTS)
    assert _read_csv(tmp_path / "description_backfill_dry_run.csv") == [
        {
            "alpha_id": "ready-alpha",
            "sync_id": "sync-current",
            "alpha_type": "REGULAR",
            "eligibility_status": "SUBMIT_READY_EXCEPT_DESCRIPTION",
            "description_status": "VALIDATED",
            "status": "DRY_RUN",
            "reason": "DESCRIPTION_REQUIRED",
            "endpoint_calls": "0",
        }
    ]
    failures = _read_csv(tmp_path / "description_validation_failures.csv")
    assert {(row["alpha_id"], row["description_status"], row["reasons"]) for row in failures} == {
        ("failed-alpha", "FAILED", "VALIDATION_FAILED"),
        ("unknown-alpha", "SCHEMA_UNKNOWN", "DESCRIPTION_SCHEMA_UNKNOWN"),
    }
    assert _read_csv(tmp_path / "historical_alpha_eligibility.csv") == [
        {
            "alpha_id": "ready-alpha",
            "sync_id": "sync-current",
            "eligibility_status": "SUBMIT_READY_EXCEPT_DESCRIPTION",
            "reasons": "DESCRIPTION_REQUIRED",
            "source": "CURRENT_PLATFORM_LEDGER_AND_SNAPSHOT",
        }
    ]


def test_phase1_markdown_reports_document_the_implemented_control_paths(tmp_path: Path) -> None:
    from alpha_mining.audit.acceptance import run_acceptance_audit
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "documented.sqlite"
    migrate(database)
    _seed_current_description_evidence(database)

    run_acceptance_audit(database, tmp_path)

    expected_evidence = {
        "DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md": (
            "run_pipeline_cycle.py -> alpha_mining.factory.runtime.main -> FactoryOrchestrator.run_simulate",
            "FactoryOrchestrator._prepare_description",
            "DescriptionSchemaRegistry.observe_from_payload",
            "payloadPath",
            "DescriptionDelivery.patch_once",
            "GET -> PATCH -> GET",
            "FactoryOrchestrator._live_sharpe_threshold",
            "classify_baseline",
            "ResearchIdentity",
            "behavior_signature",
            "cluster_disposition",
            "rank_parents",
        ),
        "DESCRIPTION_PIPELINE_IMPLEMENTATION.md": (
            "extract_description_facts",
            "build_deterministic_description",
            "validate_description",
            "DescriptionStatus",
            "description_backfill_jobs",
            "DescriptionDelivery.patch_once",
            "GET -> PATCH -> GET",
            "inspect",
            "generate",
            "dry-run",
            "backfill",
            "resume",
        ),
        "ALPHA_QUALITY_CORRELATION_FIX.md": (
            "BaselineFirstGenerator",
            "FAR_FAIL",
            "NEAR_PASS",
            "OFAT",
            "ResearchArmTracker",
            "research_arm_metrics",
            "ResearchIdentity",
            "BehaviorRoundQuota",
            "GenerationQuota",
            "cluster_disposition",
            "rank_parents",
        ),
    }
    for filename, evidence in expected_evidence.items():
        report = (tmp_path / filename).read_text(encoding="utf-8")
        for item in evidence:
            assert item in report, (filename, item)
