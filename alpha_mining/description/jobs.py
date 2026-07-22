"""Idempotent Description backfill job persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .eligibility import EligibilityStatus, classify_alpha
from .models import DescriptionStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DescriptionJob:
    job_id: str
    sync_id: str
    alpha_id: str
    eligibility_status: EligibilityStatus
    description_status: DescriptionStatus


class DescriptionJobStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def ensure_job(
        self, *, sync_id: str, alpha: Mapping[str, Any]
    ) -> DescriptionJob | None:
        decision = classify_alpha(alpha)
        if decision.status is not EligibilityStatus.SUBMIT_READY_EXCEPT_DESCRIPTION:
            return None
        alpha_id = str(alpha.get("alpha_id") or "").strip()
        if not sync_id or not alpha_id:
            return None
        job_id = hashlib.sha256(f"{sync_id}\0{alpha_id}".encode("utf-8")).hexdigest()
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT INTO description_backfill_jobs
                (job_id,sync_id,alpha_id,alpha_type,eligibility_status,description_status,
                 created_at,updated_at,job_stage)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(sync_id,alpha_id) DO UPDATE SET
                 eligibility_status=excluded.eligibility_status,updated_at=excluded.updated_at""",
                (
                    job_id,
                    sync_id,
                    alpha_id,
                    str(alpha.get("alpha_type") or "UNKNOWN").upper(),
                    decision.status.value,
                    DescriptionStatus.REQUIRED.value,
                    now,
                    now,
                    "DESCRIPTION_REQUIRED",
                ),
            )
            row = con.execute(
                """SELECT job_id,sync_id,alpha_id,eligibility_status,description_status
                   FROM description_backfill_jobs WHERE sync_id=? AND alpha_id=?""",
                (sync_id, alpha_id),
            ).fetchone()
        assert row is not None
        return DescriptionJob(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            EligibilityStatus(str(row[3])),
            DescriptionStatus(str(row[4])),
        )
