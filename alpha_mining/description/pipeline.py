"""Offline-first Description preparation; no platform write occurs here."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .eligibility import EligibilityStatus, classify_alpha
from .engine import DescriptionDraft, build_deterministic_description
from .facts import extract_description_facts
from .jobs import DescriptionJobStore
from .models import DescriptionStatus
from .schema import DescriptionSchemaRegistry
from .validator import DescriptionValidation, validate_description


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class PreparedDescription:
    job_id: str
    draft: DescriptionDraft
    validation: DescriptionValidation


class DescriptionPipeline:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.schemas = DescriptionSchemaRegistry(database)
        self.jobs = DescriptionJobStore(database)

    def prepare(
        self,
        *,
        sync_id: str,
        alpha: Mapping[str, Any],
        expression: str,
        field_metadata: Mapping[str, Mapping[str, Any]],
        operator_definitions: Mapping[str, str],
        hypothesis: Mapping[str, Any],
        settings: Mapping[str, Any],
    ) -> PreparedDescription | None:
        decision = classify_alpha(alpha)
        alpha_id = str(alpha.get("alpha_id") or "")
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT INTO alpha_eligibility_snapshots
                (sync_id,alpha_id,eligibility_status,reasons_json,classified_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(sync_id,alpha_id) DO UPDATE SET
                 eligibility_status=excluded.eligibility_status,
                 reasons_json=excluded.reasons_json,classified_at=excluded.classified_at""",
                (sync_id, alpha_id, decision.status.value, _canonical(decision.reasons), now),
            )
        if decision.status is not EligibilityStatus.SUBMIT_READY_EXCEPT_DESCRIPTION:
            return None
        job = self.jobs.ensure_job(sync_id=sync_id, alpha=alpha)
        if job is None:
            return None
        schema = self.schemas.resolve(str(alpha.get("alpha_type") or "UNKNOWN"))
        if schema is None:
            self._fail(job.job_id, DescriptionStatus.SCHEMA_UNKNOWN, "description schema unavailable")
            return None
        try:
            facts = extract_description_facts(
                alpha_type=str(alpha.get("alpha_type") or "UNKNOWN"),
                expression=expression,
                field_metadata=field_metadata,
                operator_definitions=operator_definitions,
                hypothesis=hypothesis,
                settings=settings,
            )
            draft = build_deterministic_description(facts, schema)
            validation = validate_description(draft, facts, schema)
        except Exception as exc:
            self._fail(job.job_id, DescriptionStatus.FAILED, f"{type(exc).__name__}: {exc}")
            return None
        payload_hash = hashlib.sha256(_canonical(draft.payload).encode("utf-8")).hexdigest()
        status = DescriptionStatus.VALIDATED if validation.valid else DescriptionStatus.FAILED
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE description_backfill_jobs SET description_status=?,job_stage=?,
                   description_payload_hash=?,description_payload_json=?,description_facts_json=?,
                   validation_errors_json=?,schema_hash=?,facts_hash=?,last_error=?,updated_at=?
                   WHERE job_id=?""",
                (
                    status.value,
                    status.value,
                    payload_hash,
                    _canonical(draft.payload),
                    _canonical(asdict(facts)),
                    _canonical(validation.errors),
                    schema.schema_hash,
                    facts.facts_hash,
                    "" if validation.valid else ";".join(validation.errors),
                    _utc_now(),
                    job.job_id,
                ),
            )
        return PreparedDescription(job.job_id, draft, validation)

    def _fail(
        self, job_id: str, status: DescriptionStatus, error: str
    ) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE description_backfill_jobs SET description_status=?,job_stage=?,
                   last_error=?,updated_at=? WHERE job_id=?""",
                (status.value, status.value, error, _utc_now(), job_id),
            )
