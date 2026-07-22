"""Single-attempt submission with mandatory platform reconciliation."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class SubmissionGateway(Protocol):
    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]: ...
    def submit_alpha(self, alpha_id: str) -> dict[str, Any]: ...


class SubmissionStatus(str, Enum):
    NOT_EXECUTED = "NOT_EXECUTED"
    VERIFIED = "VERIFIED"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SubmissionResult:
    status: SubmissionStatus
    intent_id: str
    error: str = ""


class SubmissionDelivery:
    def __init__(self, database: str | Path, gateway: SubmissionGateway) -> None:
        self.database = Path(database)
        self.gateway = gateway

    def _intent(self, sync_id: str, alpha_id: str) -> tuple[str, str | None]:
        payload_hash = hashlib.sha256(b"submit-once").hexdigest()
        intent_id = hashlib.sha256(
            f"{sync_id}\0{alpha_id}\0SUBMIT\0{payload_hash}".encode("utf-8")
        ).hexdigest()
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT OR IGNORE INTO platform_write_intents
                (intent_id,sync_id,alpha_id,operation,payload_hash,status,attempt_count,created_at,updated_at)
                VALUES (?,?,?,?,?,'PENDING',0,?,?)""",
                (intent_id, sync_id, alpha_id, "SUBMIT", payload_hash, now, now),
            )
            row = con.execute(
                "SELECT status FROM platform_write_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        return intent_id, str(row[0]) if row else None

    def submit_once(
        self, *, sync_id: str, alpha_id: str, execute: bool
    ) -> SubmissionResult:
        if not execute:
            return SubmissionResult(SubmissionStatus.NOT_EXECUTED, "")
        intent_id, existing = self._intent(sync_id, alpha_id)
        if existing == "CONFIRMED":
            return SubmissionResult(SubmissionStatus.VERIFIED, intent_id)
        before = self.gateway.fetch_alpha(alpha_id)
        before_status = str(before.get("status") or "UNKNOWN").upper()
        if before_status in {"SUBMITTED", "ACTIVE", "PRODUCTION"}:
            self._finish(intent_id, "CONFIRMED", None, "")
            return SubmissionResult(SubmissionStatus.VERIFIED, intent_id)
        if before_status != "UNSUBMITTED" or existing in {"PROCESSING", "UNCERTAIN"}:
            return SubmissionResult(SubmissionStatus.UNCERTAIN, intent_id, f"platform status {before_status}")
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE platform_write_intents SET status='PROCESSING',attempt_count=attempt_count+1,
                   updated_at=? WHERE intent_id=? AND status='PENDING'""",
                (_utc_now(), intent_id),
            )
        error = ""
        http_status: int | None = None
        try:
            response = self.gateway.submit_alpha(alpha_id)
            http_status = int(response.get("status_code") or 0)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        after = self.gateway.fetch_alpha(alpha_id)
        after_status = str(after.get("status") or "UNKNOWN").upper()
        if after_status in {"SUBMITTED", "ACTIVE", "PRODUCTION"}:
            self._finish(intent_id, "CONFIRMED", http_status, error)
            return SubmissionResult(SubmissionStatus.VERIFIED, intent_id, error)
        if error:
            self._finish(intent_id, "UNCERTAIN", http_status, error, complete=False)
            return SubmissionResult(SubmissionStatus.UNCERTAIN, intent_id, error)
        self._finish(intent_id, "FAILED", http_status, f"platform status {after_status}")
        return SubmissionResult(
            SubmissionStatus.FAILED, intent_id, f"platform status {after_status}"
        )

    def _finish(
        self,
        intent_id: str,
        status: str,
        http_status: int | None,
        error: str,
        *,
        complete: bool = True,
    ) -> None:
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE platform_write_intents SET status=?,last_http_status=?,last_error=?,
                   updated_at=?,completed_at=? WHERE intent_id=?""",
                (status, http_status, error, now, now if complete else None, intent_id),
            )
