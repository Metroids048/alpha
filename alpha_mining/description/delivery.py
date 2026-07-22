"""Single-attempt Description PATCH with mandatory GET reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .models import DescriptionStatus


class DescriptionGateway(Protocol):
    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]: ...
    def patch_alpha(self, alpha_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _get_path(value: object, path: tuple[str, ...]):
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


@dataclass(frozen=True)
class DeliveryResult:
    status: DescriptionStatus
    uncertain: bool
    before_hash: str
    after_hash: str
    intent_id: str
    error: str = ""


class DescriptionDelivery:
    def __init__(self, database: str | Path, gateway: DescriptionGateway) -> None:
        self.database = Path(database)
        self.gateway = gateway

    def patch_once(
        self,
        *,
        sync_id: str,
        alpha_id: str,
        alpha_type: str,
        payload: dict[str, Any],
        payload_path: tuple[str, ...],
        execute: bool,
    ) -> DeliveryResult:
        payload_hash = _hash(payload)
        if not execute:
            return DeliveryResult(DescriptionStatus.VALIDATED, False, "", "", "")
        before = self.gateway.fetch_alpha(alpha_id)
        before_hash = _hash(before)
        expected_version = str(before.get("version") or before.get("updated_at") or before.get("dateModified") or "")
        intent_id = uuid.uuid4().hex
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT INTO platform_write_intents
                (intent_id,sync_id,alpha_id,operation,payload_hash,expected_version,status,
                 attempt_count,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'PENDING',1,?,?)""",
                (intent_id, sync_id, alpha_id, "DESCRIPTION_PATCH", payload_hash, expected_version, now, now),
            )
        error = ""
        http_status: int | None = None
        try:
            response = self.gateway.patch_alpha(alpha_id, payload)
            http_status = int(response.get("status_code") or 0)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        after = self.gateway.fetch_alpha(alpha_id)
        after_hash = _hash(after)
        matches = _get_path(after, payload_path) == _get_path(payload, payload_path)
        if matches:
            status = DescriptionStatus.VERIFIED
            intent_status = "CONFIRMED"
            uncertain = False
        elif error:
            status = DescriptionStatus.PATCH_PENDING
            intent_status = "UNCERTAIN"
            uncertain = True
        else:
            status = DescriptionStatus.FAILED
            intent_status = "FAILED"
            uncertain = False
        with sqlite3.connect(self.database) as con:
            con.execute(
                """UPDATE platform_write_intents SET status=?,last_http_status=?,last_error=?,
                   updated_at=?,completed_at=? WHERE intent_id=?""",
                (
                    intent_status,
                    http_status,
                    error,
                    _utc_now(),
                    _utc_now() if intent_status in {"CONFIRMED", "FAILED"} else None,
                    intent_id,
                ),
            )
        return DeliveryResult(status, uncertain, before_hash, after_hash, intent_id, error)
