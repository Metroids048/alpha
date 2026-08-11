"""Transactional persistence for simulation request state transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field as dataclasses_field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from alpha_mining.domain.expression_normalization import expression_identity


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    reason: str
    request_hash: str = ""


@dataclass(frozen=True)
class RequestLease:
    request_hash: str
    expression: str
    settings: dict[str, Any]
    lease_started_at: str
    progress_location: str = ""
    alpha_id: str = ""
    context: dict[str, Any] = dataclasses_field(default_factory=dict)


class SimulationRequestStore:
    """The only component allowed to mutate simulation request state."""

    def __init__(
        self,
        database: str | Path,
        *,
        lease_timeout_seconds: float = 900.0,
        now: Callable[[], str] = _utc_now,
        settings_contract: Any | None = None,
    ) -> None:
        self.database = Path(database)
        self.lease_timeout_seconds = max(1.0, float(lease_timeout_seconds))
        self._now = now
        self.settings_contract = settings_contract

    def claim(
        self,
        expression: str,
        settings: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        allow_existing_identity: bool = False,
    ) -> ClaimResult:
        alpha_type = str(settings.get("alpha_type") or "REGULAR").upper()
        if self.settings_contract is not None:
            try:
                # The outer type must be resolved before prepare() strips it:
                # the platform refuses alpha_type inside "settings", so reading
                # it back from the prepared object would silently default here.
                alpha_type = str(self.settings_contract.alpha_type(settings)).upper()
                settings = self.settings_contract.prepare(settings)
            except ValueError:
                return ClaimResult(False, "invalid_simulation_settings")
        identity = expression_identity(expression)
        if not identity.parameter_skeleton or not identity.field_skeleton:
            return ClaimResult(False, "invalid_identity")
        payload = {"type": alpha_type, "regular": expression, "settings": settings}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = self._now()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as con:
            try:
                con.execute("BEGIN IMMEDIATE")
                resumable = con.execute(
                    """SELECT 1 FROM simulation_requests
                       WHERE request_hash=? AND status='PENDING' AND last_error LIKE 'AUTH_PAUSED:%'""",
                    (request_hash,),
                ).fetchone()
                if resumable:
                    con.execute(
                        "UPDATE factory_candidate_claims SET status='CLAIMED',updated_at=? WHERE request_hash=?",
                        (now, request_hash),
                    )
                    con.commit()
                    return ClaimResult(True, "resuming_pending_request", request_hash)
                historical = con.execute(
                    """SELECT 1 FROM expression_identities WHERE exact_hash=?
                       UNION ALL
                       SELECT 1 FROM factory_candidate_claims WHERE exact_hash=?
                       LIMIT 1""",
                    (identity.exact_hash, identity.exact_hash),
                ).fetchone()
                if historical and not allow_existing_identity:
                    con.rollback()
                    return ClaimResult(False, "exact_hash_exists", request_hash)
                if not historical:
                    con.execute(
                        """INSERT INTO factory_candidate_claims
                        (expression_text,exact_hash,parameter_skeleton,field_skeleton,request_hash,
                         status,created_at,updated_at)
                        VALUES (?,?,?,?,?,'CLAIMED',?,?)""",
                        (
                            expression,
                            identity.exact_hash,
                            identity.parameter_skeleton,
                            identity.field_skeleton,
                            request_hash,
                            now,
                            now,
                        ),
                    )
                elif not bool((context or {}).get("tune_parent_candidate_id")):
                    con.rollback()
                    return ClaimResult(False, "tune_lineage_required", request_hash)
                elif not con.execute(
                    """SELECT 1 FROM candidate_outcomes
                       WHERE candidate_id=? AND exact_hash=? LIMIT 1""",
                    (str((context or {}).get("tune_parent_candidate_id") or ""), identity.exact_hash),
                ).fetchone():
                    con.rollback()
                    return ClaimResult(False, "tune_parent_unverified", request_hash)
                context_json = json.dumps(context or {}, sort_keys=True)
                con.execute(
                    """INSERT INTO simulation_requests
                    (request_hash,payload_json,context_json,status,created_at,updated_at)
                    VALUES (?,?,?,'PENDING',?,?)""",
                    (request_hash, encoded, context_json, now, now),
                )
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
                return ClaimResult(False, "identity_conflict", request_hash)
            except BaseException:
                con.rollback()
                raise
        return ClaimResult(True, "claimed", request_hash)

    def _mark_stale_requests(self, con: sqlite3.Connection, cutoff: str, now: str) -> None:
        stale_without_checkpoint = con.execute(
            """SELECT request_hash FROM simulation_requests
               WHERE status='IN_PROGRESS' AND lease_started_at<>''
                 AND lease_started_at<? AND progress_location='' AND alpha_id=''""",
            (cutoff,),
        ).fetchall()
        for (request_hash,) in stale_without_checkpoint:
            con.execute(
                """UPDATE simulation_requests
                   SET status='UNKNOWN',last_error=?,updated_at=?
                   WHERE request_hash=? AND status='IN_PROGRESS'""",
                ("lease expired without external checkpoint", now, request_hash),
            )
            con.execute(
                "UPDATE factory_candidate_claims SET status='UNKNOWN',updated_at=? WHERE request_hash=?",
                (now, request_hash),
            )

    def acquire(self, limit: int, *, request_hash: str = "") -> list[RequestLease]:
        if limit <= 0:
            return []
        now = self._now()
        current_dt = _parse_time(now) or datetime.now(timezone.utc)
        cutoff_dt = current_dt - timedelta(seconds=self.lease_timeout_seconds)
        cutoff = cutoff_dt.isoformat().replace("+00:00", "Z")
        leases: list[RequestLease] = []
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            self._mark_stale_requests(con, cutoff, now)
            rows = con.execute(
                """SELECT request_hash,payload_json,progress_location,alpha_id,status,
                          lease_started_at,COALESCE(context_json,'{}')
                   FROM simulation_requests
                   WHERE (status='PENDING'
                      OR (status='IN_PROGRESS' AND lease_started_at<?
                          AND (progress_location<>'' OR alpha_id<>'')))
                     AND (?='' OR request_hash=?)
                   ORDER BY created_at LIMIT ?""",
                (cutoff, str(request_hash), str(request_hash), int(limit)),
            ).fetchall()
            for request_hash, payload_json, location, alpha_id, status, lease_started_at, context_json in rows:
                eligible = status == "PENDING" or (
                    status == "IN_PROGRESS"
                    and (_parse_time(str(lease_started_at)) is not None)
                    and str(lease_started_at) < cutoff
                )
                if not eligible:
                    continue
                if status == "PENDING":
                    updated = con.execute(
                        """UPDATE simulation_requests
                           SET status='IN_PROGRESS',attempt_count=attempt_count+1,
                               lease_started_at=?,updated_at=?
                           WHERE request_hash=? AND status='PENDING'""",
                        (now, now, request_hash),
                    )
                else:
                    updated = con.execute(
                        """UPDATE simulation_requests
                           SET attempt_count=attempt_count+1,lease_started_at=?,updated_at=?
                           WHERE request_hash=? AND status='IN_PROGRESS'
                             AND lease_started_at=?
                             AND (progress_location<>'' OR alpha_id<>'')""",
                        (now, now, request_hash, lease_started_at),
                    )
                if updated.rowcount != 1:
                    continue
                try:
                    payload = json.loads(payload_json)
                    expression = str(payload.get("regular") or "").strip()
                    settings = payload.get("settings")
                except (TypeError, ValueError, AttributeError):
                    expression, settings = "", None
                if not expression or not isinstance(settings, dict):
                    con.execute(
                        """UPDATE simulation_requests SET status='FAILED',last_error=?,updated_at=?
                           WHERE request_hash=?""",
                        ("request payload is invalid", now, request_hash),
                    )
                    con.execute(
                        "UPDATE factory_candidate_claims SET status='FAILED',updated_at=? WHERE request_hash=?",
                        (now, request_hash),
                    )
                    continue
                try:
                    context_dict: dict[str, Any] = json.loads(context_json or "{}")
                    if not isinstance(context_dict, dict):
                        context_dict = {}
                except (TypeError, ValueError):
                    context_dict = {}
                leases.append(
                    RequestLease(
                        str(request_hash),
                        expression,
                        dict(settings),
                        now,
                        str(location or ""),
                        str(alpha_id or ""),
                        context_dict,
                    )
                )
            con.commit()
        return leases

    def checkpoint(
        self,
        request_hash: str,
        *,
        lease_started_at: str,
        progress_location: str = "",
        alpha_id: str = "",
    ) -> None:
        if not str(progress_location or "").strip() and not str(alpha_id or "").strip():
            return
        now = self._now()
        with sqlite3.connect(self.database) as con:
            updated = con.execute(
                """UPDATE simulation_requests
                   SET progress_location=CASE WHEN ?<>'' THEN ? ELSE progress_location END,
                       alpha_id=CASE WHEN ?<>'' THEN ? ELSE alpha_id END,
                       updated_at=?
                   WHERE request_hash=? AND status='IN_PROGRESS' AND lease_started_at=?""",
                (
                    str(progress_location or "").strip(),
                    str(progress_location or "").strip(),
                    str(alpha_id or "").strip(),
                    str(alpha_id or "").strip(),
                    now,
                    request_hash,
                    lease_started_at,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("simulation checkpoint no longer owns an active lease")

    def finalize_failure(
        self,
        request_hash: str,
        *,
        lease_started_at: str,
        status: str = "FAILED",
        error: str = "",
    ) -> bool:
        terminal = "UNKNOWN" if status.upper() == "UNKNOWN" else "FAILED"
        now = self._now()
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            updated = con.execute(
                """UPDATE simulation_requests SET status=?,last_error=?,updated_at=?
                   WHERE request_hash=? AND status='IN_PROGRESS' AND lease_started_at=?""",
                (terminal, str(error)[:1000], now, request_hash, lease_started_at),
            )
            if updated.rowcount == 1:
                con.execute(
                    "UPDATE factory_candidate_claims SET status=?,updated_at=? WHERE request_hash=?",
                    (terminal, now, request_hash),
                )
            con.commit()
        return updated.rowcount == 1

    def defer_for_authentication(
        self, request_hash: str, *, lease_started_at: str, error: str = ""
    ) -> bool:
        """Release an authenticated request without losing its checkpoint."""

        now = self._now()
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            updated = con.execute(
                """UPDATE simulation_requests SET status='PENDING',last_error=?,updated_at=?
                   WHERE request_hash=? AND status='IN_PROGRESS' AND lease_started_at=?""",
                ("AUTH_PAUSED: " + str(error)[:980], now, request_hash, lease_started_at),
            )
            if updated.rowcount == 1:
                con.execute(
                    "UPDATE factory_candidate_claims SET status='CLAIMED',updated_at=? WHERE request_hash=?",
                    (now, request_hash),
                )
            con.commit()
        return updated.rowcount == 1

    def finalize_success(
        self,
        request_hash: str,
        *,
        alpha_id: str,
        lease_started_at: str,
        write_success: Callable[[sqlite3.Connection], None],
    ) -> bool:
        if not str(alpha_id or "").strip():
            raise ValueError("successful request requires a non-empty alpha_id")
        now = self._now()
        with sqlite3.connect(self.database) as con:
            try:
                con.execute("BEGIN IMMEDIATE")
                owns_lease = con.execute(
                    """SELECT 1 FROM simulation_requests
                       WHERE request_hash=? AND status='IN_PROGRESS' AND lease_started_at=?""",
                    (request_hash, lease_started_at),
                ).fetchone()
                if not owns_lease:
                    con.rollback()
                    return False
                write_success(con)
                updated = con.execute(
                    """UPDATE simulation_requests SET status='COMPLETE',alpha_id=?,updated_at=?
                       WHERE request_hash=? AND status='IN_PROGRESS' AND lease_started_at=?""",
                    (str(alpha_id).strip(), now, request_hash, lease_started_at),
                )
                if updated.rowcount != 1:
                    raise sqlite3.IntegrityError("request is no longer in progress")
                con.execute(
                    "UPDATE factory_candidate_claims SET status='SIMULATED',updated_at=? WHERE request_hash=?",
                    (now, request_hash),
                )
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return True
