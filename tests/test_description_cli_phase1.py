"""Phase 1 safety contracts for the public description CLI."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "argv",
    [
        ["description", "inspect", "--alpha-id", "alpha-1"],
        ["description", "generate", "--alpha-id", "alpha-1"],
        ["description", "validate", "--alpha-id", "alpha-1"],
        ["description", "dry-run", "--alpha-id", "alpha-1"],
        ["description", "patch", "--alpha-id", "alpha-1"],
        ["description", "verify", "--alpha-id", "alpha-1"],
        ["description", "backfill", "--dry-run"],
        [
            "description",
            "backfill",
            "--execute",
            "--confirm",
            "I_UNDERSTAND_PLATFORM_WRITES",
        ],
        ["description", "resume", "--job-id", "job-1"],
    ],
)
def test_description_command_family_parses(argv: list[str]) -> None:
    from alpha_mining.main import _build_parser

    parsed = _build_parser().parse_args(argv)

    assert parsed.command == "description"
    assert callable(parsed.func)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_local_description_evidence(
    database: Path,
    alpha_id: str,
    *,
    sync_status: str = "COMPLETE",
    synced_at: str | None = None,
    with_schema: bool = True,
) -> str:
    from alpha_mining.description.pipeline import DescriptionPipeline
    from alpha_mining.description.schema import DescriptionSchemaRegistry
    from alpha_mining.storage.migrations import migrate

    migrate(database)
    synced_at = synced_at or _now()
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO platform_sync_runs
            (sync_id,filters_json,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,
             status,error_message,started_at,completed_at)
            VALUES ('sync-1','{}',1,1,1,0,?,'',?,?)""",
            (sync_status, synced_at, synced_at),
        )
        con.execute(
            """INSERT INTO platform_alpha_ledger
            (alpha_id,sync_id,platform_status,alpha_type,expression_hash,settings_hash,
             synced_at,raw_payload_hash)
            VALUES (?, 'sync-1', 'UNSUBMITTED', 'REGULAR', 'expression', 'settings',
                    '2026-01-01T00:00:00Z', 'payload')""",
            (alpha_id,),
        )
        con.execute(
            """UPDATE factory_control SET hard_stop=0,reason='',ledger_sync_id='sync-1',
               cluster_freeze_complete=1,execute_description_patch=1 WHERE singleton=1"""
        )
    if not with_schema:
        return ""
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
        alpha={
            "alpha_id": alpha_id,
            "alpha_type": "REGULAR",
            "platform_status": "UNSUBMITTED",
            "checks_fresh": True,
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS"},
                {"name": "SELF_CORRELATION", "result": "PASS"},
                {"name": "PROD_CORRELATION", "result": "PASS"},
            ],
            "description_required": True,
            "description_valid": False,
            "schema_known": True,
        },
        expression="rank(close)",
        field_metadata={"close": {"description": "closing price"}},
        operator_definitions={"rank": "cross-sectional rank"},
        hypothesis={"mechanism": "relative price level", "expected_direction": "higher_is_long"},
        settings={"delay": 1, "neutralization": "SUBINDUSTRY"},
    )
    assert prepared is not None and prepared.validation.valid
    return prepared.job_id


def test_inspect_reads_latest_local_ledger_without_platform_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.main import main

    database = tmp_path / "description.sqlite"
    _seed_local_description_evidence(database, "alpha-1")

    assert main(["description", "inspect", "--database", str(database), "--alpha-id", "alpha-1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["sync_id"] == "sync-1"
    assert payload["platform_status"] == "UNSUBMITTED"


def test_patch_defaults_to_fail_closed_without_creating_a_platform_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.main import main

    database = tmp_path / "description.sqlite"
    _seed_local_description_evidence(database, "alpha-1")

    assert main(["description", "patch", "--database", str(database), "--alpha-id", "alpha-1"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "command": "patch",
        "platform_client_created": False,
        "reason": "WRITE_CONFIRMATION_REQUIRED",
        "status": "BLOCKED",
    }


@pytest.mark.parametrize(
    ("sync_status", "synced_at", "reason"),
    [
        ("PARTIAL", None, "LOCAL_LEDGER_SYNC_NOT_COMPLETE"),
        ("COMPLETE", (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z"), "LOCAL_LEDGER_SYNC_STALE"),
    ],
)
def test_inspect_fails_closed_for_noncomplete_or_stale_ledger(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_status: str,
    synced_at: str | None,
    reason: str,
) -> None:
    from alpha_mining.main import main

    database = tmp_path / "description.sqlite"
    _seed_local_description_evidence(database, "alpha-1", sync_status=sync_status, synced_at=synced_at)

    assert main(["description", "inspect", "--database", str(database), "--alpha-id", "alpha-1"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == reason


def test_inspect_fails_closed_for_unknown_ledger_row_or_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.main import main

    unknown_row = tmp_path / "unknown-row.sqlite"
    _seed_local_description_evidence(unknown_row, "alpha-1")
    assert main(["description", "inspect", "--database", str(unknown_row), "--alpha-id", "absent"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "LOCAL_LEDGER_ROW_NOT_FOUND"

    unknown_schema = tmp_path / "unknown-schema.sqlite"
    _seed_local_description_evidence(unknown_schema, "alpha-1", with_schema=False)
    assert main(["description", "inspect", "--database", str(unknown_schema), "--alpha-id", "alpha-1"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "DESCRIPTION_SCHEMA_UNKNOWN"


class _Gateway:
    def __init__(self, payload: dict[str, object]) -> None:
        self.calls: list[str] = []
        self.alpha = {"id": "alpha-1", "version": "1", "description": {"text": "old"}}
        self.payload = payload

    def fetch_alpha(self, alpha_id: str) -> dict[str, object]:
        self.calls.append("GET")
        return json.loads(json.dumps(self.alpha))

    def patch_alpha(self, alpha_id: str, payload: dict[str, object]) -> dict[str, int]:
        self.calls.append("PATCH")
        self.alpha["description"] = json.loads(json.dumps(payload))["description"]
        self.alpha["version"] = "2"
        return {"status_code": 200}


class _SelectiveGateway(_Gateway):
    def __init__(self, payload: dict[str, object], *, fail_alpha_id: str) -> None:
        super().__init__(payload)
        self.fail_alpha_id = fail_alpha_id

    def patch_alpha(self, alpha_id: str, payload: dict[str, object]) -> dict[str, int]:
        if alpha_id == self.fail_alpha_id:
            self.calls.append("PATCH")
            raise TimeoutError("simulated ambiguous patch")
        return super().patch_alpha(alpha_id, payload)


def _persisted_payload(database: Path) -> dict[str, object]:
    with sqlite3.connect(database) as con:
        row = con.execute("SELECT description_payload_json FROM description_backfill_jobs").fetchone()
    return json.loads(row[0])


def _copy_validated_job(database: Path, *, alpha_id: str, sync_id: str, job_id: str) -> None:
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO description_backfill_jobs
            (job_id,sync_id,alpha_id,alpha_type,eligibility_status,description_status,
             description_payload_hash,description_payload_json,description_facts_json,
             validation_errors_json,created_at,updated_at,job_stage,schema_hash,facts_hash)
            SELECT ?,?,? ,alpha_type,eligibility_status,description_status,
                   description_payload_hash,description_payload_json,description_facts_json,
                   validation_errors_json,created_at,updated_at,job_stage,schema_hash,facts_hash
            FROM description_backfill_jobs WHERE alpha_id='alpha-1' AND sync_id='sync-1'""",
            (job_id, sync_id, alpha_id),
        )


def _insert_current_ledger_alpha(database: Path, alpha_id: str, sync_id: str = "sync-1") -> None:
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO platform_alpha_ledger
            (alpha_id,sync_id,platform_status,alpha_type,expression_hash,settings_hash,
             synced_at,raw_payload_hash)
            VALUES (?, ?, 'UNSUBMITTED', 'REGULAR', 'expression', 'settings', ?, 'payload')""",
            (alpha_id, sync_id, _now()),
        )


def test_patch_uses_get_single_patch_get_after_all_write_preconditions(tmp_path: Path) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "patch.sqlite"
    _seed_local_description_evidence(database, "alpha-1")
    gateway = _Gateway(_persisted_payload(database))
    service = DescriptionCliService(database, gateway_factory=lambda: gateway)

    assert service.patch("alpha-1", "I_UNDERSTAND_PLATFORM_WRITES") == 0
    assert gateway.calls == ["GET", "PATCH", "GET"]
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT description_status FROM description_backfill_jobs").fetchone()[0] == "VERIFIED"


def test_verify_uses_one_read_only_get_and_validate_rechecks_persisted_payload(tmp_path: Path) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "verify.sqlite"
    _seed_local_description_evidence(database, "alpha-1")
    payload = _persisted_payload(database)
    gateway = _Gateway(payload)
    gateway.alpha["description"] = payload["description"]
    service = DescriptionCliService(database, gateway_factory=lambda: gateway)

    assert service.verify("alpha-1") == 0
    assert gateway.calls == ["GET"]
    with sqlite3.connect(database) as con:
        con.execute("UPDATE description_backfill_jobs SET description_payload_json='{}'")
    assert service.validate("alpha-1") == 2
    with sqlite3.connect(database) as con:
        assert "PAYLOAD_PATH_MISMATCH" in con.execute(
            "SELECT validation_errors_json FROM description_backfill_jobs"
        ).fetchone()[0]


def test_backfill_execute_patches_validated_jobs_without_submission(tmp_path: Path) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "backfill.sqlite"
    _seed_local_description_evidence(database, "alpha-1")
    gateway = _Gateway(_persisted_payload(database))
    service = DescriptionCliService(database, gateway_factory=lambda: gateway)

    assert service.backfill(dry_run=False, execute=True, confirmation="I_UNDERSTAND_PLATFORM_WRITES") == 0
    assert gateway.calls == ["GET", "PATCH", "GET"]


def test_backfill_uses_only_validated_job_matching_current_ledger_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "backfill-sync.sqlite"
    _seed_local_description_evidence(database, "alpha-1")
    with sqlite3.connect(database) as con:
        now = _now()
        con.execute(
            """INSERT INTO platform_sync_runs
            (sync_id,filters_json,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,
             status,error_message,started_at,completed_at)
            VALUES ('sync-2','{}',1,1,1,0,'COMPLETE','',?,?)""",
            (now, now),
        )
        con.execute("UPDATE platform_alpha_ledger SET sync_id='sync-2',synced_at=? WHERE alpha_id='alpha-1'", (now,))
        con.execute("UPDATE factory_control SET ledger_sync_id='sync-2' WHERE singleton=1")
    _copy_validated_job(database, alpha_id="alpha-1", sync_id="sync-2", job_id="job-sync-2")
    gateway = _Gateway(_persisted_payload(database))
    service = DescriptionCliService(database, gateway_factory=lambda: gateway)

    assert service.backfill(dry_run=False, execute=True, confirmation="I_UNDERSTAND_PLATFORM_WRITES") == 0
    assert gateway.calls == ["GET", "PATCH", "GET"]
    assert json.loads(capsys.readouterr().out)["candidates"] == 1
    with sqlite3.connect(database) as con:
        assert con.execute(
            "SELECT description_status FROM description_backfill_jobs WHERE job_id='7152ddcd8855f664ef3f78a191e139c1c386d079530e027670db58e961fd95ad'"
        ).fetchone()[0] == "VALIDATED"
        assert con.execute("SELECT description_status FROM description_backfill_jobs WHERE job_id='job-sync-2'").fetchone()[0] == "VERIFIED"


def test_backfill_execute_emits_one_accurate_json_summary_on_partial_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "backfill-partial.sqlite"
    _seed_local_description_evidence(database, "alpha-1")
    _insert_current_ledger_alpha(database, "alpha-2")
    _copy_validated_job(database, alpha_id="alpha-2", sync_id="sync-1", job_id="job-alpha-2")
    gateway = _SelectiveGateway(_persisted_payload(database), fail_alpha_id="alpha-1")
    service = DescriptionCliService(database, gateway_factory=lambda: gateway)

    assert service.backfill(dry_run=False, execute=True, confirmation="I_UNDERSTAND_PLATFORM_WRITES") == 2
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "blocked": 1,
        "candidates": 2,
        "command": "backfill",
        "mode": "execute",
        "patched": 1,
        "status": "BLOCKED",
    }


def test_resume_is_idempotent_for_stable_job_and_blocks_uncertain_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from alpha_mining.description.cli import DescriptionCliService

    database = tmp_path / "resume.sqlite"
    job_id = _seed_local_description_evidence(database, "alpha-1")
    service = DescriptionCliService(database)
    with sqlite3.connect(database) as con:
        before = con.execute("SELECT updated_at FROM description_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()[0]
    assert service.resume(job_id) == 0
    first = json.loads(capsys.readouterr().out)
    assert service.resume(job_id) == 0
    second = json.loads(capsys.readouterr().out)
    with sqlite3.connect(database) as con:
        after = con.execute("SELECT updated_at FROM description_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        con.execute("UPDATE description_backfill_jobs SET uncertain_write=1 WHERE job_id=?", (job_id,))
    assert before == after
    assert first == second
    assert service.resume(job_id) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "UNCERTAIN_DESCRIPTION_JOB"
    assert service.resume("unknown-job") == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "UNKNOWN_DESCRIPTION_JOB"
