"""Fail-closed, local-ledger CLI service for alpha descriptions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .delivery import DescriptionDelivery, DescriptionGateway
from .engine import DescriptionDraft, build_deterministic_description
from .facts import DescriptionFacts
from .models import DescriptionStatus
from .schema import DescriptionSchema
from .validator import DescriptionValidation, validate_description


CONFIRMATION_PHRASE = "I_UNDERSTAND_PLATFORM_WRITES"
MAX_LEDGER_AGE = timedelta(hours=24)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _at_path(value: object, path: tuple[str, ...]) -> object | None:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


@dataclass(frozen=True)
class LocalDescriptionState:
    alpha_id: str
    sync_id: str
    alpha_type: str
    platform_status: str
    ledger_synced_at: str
    schema: DescriptionSchema
    job_id: str
    job_status: str
    payload_hash: str
    payload_json: str
    facts_json: str
    validation_errors_json: str
    uncertain_write: bool


class DescriptionCliService:
    """Use durable local evidence before constructing a network gateway."""

    def __init__(
        self,
        database: str | Path,
        *,
        gateway_factory: Callable[[], DescriptionGateway] | None = None,
    ) -> None:
        self.database = Path(database)
        self.gateway_factory = gateway_factory

    def inspect(self, alpha_id: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("inspect", reason)
        self._emit(
            "inspect",
            "OK",
            alpha_id=state.alpha_id,
            sync_id=state.sync_id,
            alpha_type=state.alpha_type,
            platform_status=state.platform_status,
            ledger_synced_at=state.ledger_synced_at,
            schema_hash=state.schema.schema_hash,
            job_id=state.job_id,
            description_status=state.job_status,
        )
        return 0

    def generate(self, alpha_id: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("generate", reason)
        facts, reason = self._facts(state)
        if facts is None:
            return self._blocked("generate", reason)
        if not state.job_id:
            return self._blocked("generate", "DESCRIPTION_JOB_NOT_FOUND")
        draft = build_deterministic_description(facts, state.schema)
        validation = validate_description(draft, facts, state.schema)
        self._persist_validation(state, draft, validation)
        if not validation.valid:
            return self._blocked("generate", "DESCRIPTION_VALIDATION_FAILED")
        self._emit("generate", "OK", alpha_id=alpha_id, job_id=state.job_id, description_status="VALIDATED")
        return 0

    def validate(self, alpha_id: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("validate", reason)
        validation, reason = self._validate_state(state)
        if validation is None:
            return self._blocked("validate", reason)
        if not validation.valid:
            return self._blocked("validate", "DESCRIPTION_VALIDATION_FAILED")
        self._emit("validate", "OK", alpha_id=alpha_id, job_id=state.job_id, description_status="VALIDATED")
        return 0

    def dry_run(self, alpha_id: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("dry-run", reason)
        validation, reason = self._validate_state(state)
        if validation is None:
            return self._blocked("dry-run", reason)
        self._emit(
            "dry-run",
            "OK" if validation.valid else "BLOCKED",
            alpha_id=state.alpha_id,
            eligibility="LOCAL_LEDGER_EVIDENCE",
            description_status="VALIDATED" if validation.valid else "FAILED",
            validation_errors=list(validation.errors),
            platform_client_created=False,
            writes=0,
        )
        return 0 if validation.valid else 2

    def patch(self, alpha_id: str, confirmation: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("patch", reason)
        if confirmation != CONFIRMATION_PHRASE:
            return self._blocked("patch", "WRITE_CONFIRMATION_REQUIRED")
        if not self._factory_patch_permitted(state.sync_id):
            return self._blocked("patch", "FACTORY_DESCRIPTION_PATCH_DISABLED")
        return self._patch_state(state)

    def verify(self, alpha_id: str) -> int:
        state, reason = self._state(alpha_id)
        if state is None:
            return self._blocked("verify", reason)
        payload, reason = self._payload(state)
        if payload is None:
            return self._blocked("verify", reason)
        if not state.job_id:
            return self._blocked("verify", "DESCRIPTION_JOB_NOT_FOUND")
        # Explicit verify is the sole read-only command that creates a gateway.
        observed = self._gateway().fetch_alpha(state.alpha_id)
        if _at_path(observed, state.schema.payload_path) != _at_path(payload, state.schema.payload_path):
            self._set_job_state(state, DescriptionStatus.PATCH_PENDING, last_error="platform description readback mismatch")
            return self._blocked("verify", "PLATFORM_DESCRIPTION_MISMATCH")
        self._set_job_state(state, DescriptionStatus.VERIFIED)
        self._emit("verify", "OK", alpha_id=state.alpha_id, job_id=state.job_id, description_status="VERIFIED")
        return 0

    def backfill(self, *, dry_run: bool, execute: bool, confirmation: str) -> int:
        if dry_run == execute:
            return self._blocked("backfill", "EXACTLY_ONE_MODE_REQUIRED")
        try:
            with self._connection() as con:
                candidates = [
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in con.execute(
                        """SELECT j.alpha_id,j.sync_id,j.job_id
                           FROM description_backfill_jobs j
                           JOIN platform_alpha_ledger l
                             ON l.alpha_id=j.alpha_id AND l.sync_id=j.sync_id
                           WHERE j.description_status='VALIDATED'
                           ORDER BY j.alpha_id,j.sync_id,j.job_id"""
                    )
                ]
        except sqlite3.Error:
            return self._blocked("backfill", "LOCAL_DESCRIPTION_SCHEMA_UNAVAILABLE")
        if dry_run:
            blocked = 0
            for alpha_id, sync_id, job_id in candidates:
                state, reason = self._state(alpha_id)
                if state is None or state.sync_id != sync_id or state.job_id != job_id:
                    blocked += 1
                    continue
                validation, _ = self._validate_state(state)
                blocked += int(validation is None or not validation.valid)
            self._emit(
                "backfill",
                "OK" if not blocked else "BLOCKED",
                candidates=len(candidates),
                blocked=blocked,
                mode="dry-run",
                platform_client_created=False,
                writes=0,
            )
            return 0 if not blocked else 2
        if confirmation != CONFIRMATION_PHRASE:
            return self._blocked("backfill", "WRITE_CONFIRMATION_REQUIRED")
        completed = 0
        blocked = 0
        for alpha_id, sync_id, job_id in candidates:
            state, reason = self._state(alpha_id)
            if (
                state is None
                or state.sync_id != sync_id
                or state.job_id != job_id
                or not self._factory_patch_permitted(sync_id)
            ):
                blocked += 1
                continue
            if self._patch_state(state, emit=False) == 0:
                completed += 1
            else:
                blocked += 1
        self._emit("backfill", "OK" if not blocked else "BLOCKED", candidates=len(candidates), patched=completed, blocked=blocked, mode="execute")
        return 0 if not blocked else 2

    def resume(self, job_id: str) -> int:
        try:
            with self._connection() as con:
                row = con.execute(
                    "SELECT alpha_id,description_status,uncertain_write FROM description_backfill_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
        except sqlite3.Error:
            return self._blocked("resume", "LOCAL_DESCRIPTION_SCHEMA_UNAVAILABLE")
        if row is None:
            return self._blocked("resume", "UNKNOWN_DESCRIPTION_JOB")
        if bool(row[2]):
            return self._blocked("resume", "UNCERTAIN_DESCRIPTION_JOB")
        state, reason = self._state(str(row[0]))
        if state is None:
            return self._blocked("resume", reason)
        self._emit("resume", "OK", job_id=job_id, alpha_id=state.alpha_id, description_status=str(row[1]), platform_client_created=False)
        return 0

    def _patch_state(self, state: LocalDescriptionState, *, emit: bool = True) -> int:
        if not state.job_id or state.job_status != DescriptionStatus.VALIDATED.value:
            return self._patch_blocked(emit, "VALIDATED_JOB_REQUIRED")
        validation, reason = self._validate_state(state)
        if validation is None:
            return self._patch_blocked(emit, reason)
        if not validation.valid:
            return self._patch_blocked(emit, "DESCRIPTION_VALIDATION_FAILED")
        payload, reason = self._payload(state)
        if payload is None:
            return self._patch_blocked(emit, reason)
        result = DescriptionDelivery(self.database, self._gateway()).patch_once(
            sync_id=state.sync_id,
            alpha_id=state.alpha_id,
            alpha_type=state.alpha_type,
            payload=payload,
            payload_path=state.schema.payload_path,
            execute=True,
        )
        self._set_job_state(
            state,
            result.status,
            before_hash=result.before_hash,
            after_hash=result.after_hash,
            intent_id=result.intent_id,
            uncertain=result.uncertain,
            increment_patch=True,
            last_error=result.error,
        )
        if result.status is not DescriptionStatus.VERIFIED:
            return self._patch_blocked(emit, "DESCRIPTION_PATCH_UNVERIFIED")
        if emit:
            self._emit("patch", "OK", alpha_id=state.alpha_id, job_id=state.job_id, description_status="VERIFIED")
        return 0

    def _patch_blocked(self, emit: bool, reason: str) -> int:
        return self._blocked("patch", reason) if emit else 2

    def _state(self, alpha_id: str) -> tuple[LocalDescriptionState | None, str]:
        if not alpha_id.strip():
            return None, "ALPHA_ID_REQUIRED"
        try:
            with self._connection() as con:
                ledger = con.execute(
                    """SELECT l.alpha_id,l.sync_id,l.alpha_type,l.platform_status,l.synced_at,
                              r.status,r.declared_count,r.fetched_rows,r.unique_alpha_ids,
                              r.duplicate_alpha_ids,r.completed_at
                       FROM platform_alpha_ledger l
                       JOIN platform_sync_runs r ON r.sync_id=l.sync_id
                       WHERE l.alpha_id=?""",
                    (alpha_id,),
                ).fetchone()
                if ledger is None:
                    return None, "LOCAL_LEDGER_ROW_NOT_FOUND"
                if str(ledger[5]).upper() != "COMPLETE":
                    return None, "LOCAL_LEDGER_SYNC_NOT_COMPLETE"
                counts = tuple(int(value) for value in ledger[6:10])
                if not counts[0] or counts[0] != counts[1] or counts[0] != counts[2] or counts[3]:
                    return None, "LOCAL_LEDGER_COUNTS_UNRECONCILED"
                try:
                    completed = datetime.fromisoformat(str(ledger[10]).replace("Z", "+00:00"))
                    if completed.tzinfo is None:
                        completed = completed.replace(tzinfo=timezone.utc)
                except ValueError:
                    return None, "LOCAL_LEDGER_TIMESTAMP_INVALID"
                if datetime.now(timezone.utc) - completed.astimezone(timezone.utc) > MAX_LEDGER_AGE:
                    return None, "LOCAL_LEDGER_SYNC_STALE"
                schema_row = con.execute(
                    """SELECT schema_id,alpha_type,payload_path_json,min_length,max_length,
                              required_sections_json,source,source_version,schema_hash,raw_schema_json
                       FROM description_schema_observations WHERE alpha_type=?
                       ORDER BY observed_at DESC,schema_id DESC LIMIT 1""",
                    (str(ledger[2]).upper(),),
                ).fetchone()
                if schema_row is None:
                    return None, "DESCRIPTION_SCHEMA_UNKNOWN"
                schema = DescriptionSchema(
                    str(schema_row[0]), str(schema_row[1]), tuple(json.loads(schema_row[2])),
                    int(schema_row[3]), int(schema_row[4]) if schema_row[4] is not None else None,
                    tuple(json.loads(schema_row[5])), str(schema_row[6]), str(schema_row[7]),
                    str(schema_row[8]), dict(json.loads(schema_row[9])),
                )
                job = con.execute(
                    """SELECT job_id,description_status,description_payload_hash,description_payload_json,
                              description_facts_json,validation_errors_json,uncertain_write
                       FROM description_backfill_jobs WHERE alpha_id=? AND sync_id=?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (alpha_id, ledger[1]),
                ).fetchone()
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return None, "LOCAL_DESCRIPTION_SCHEMA_UNAVAILABLE"
        return LocalDescriptionState(
            alpha_id=str(ledger[0]), sync_id=str(ledger[1]), alpha_type=str(ledger[2]),
            platform_status=str(ledger[3]), ledger_synced_at=str(ledger[4]), schema=schema,
            job_id=str(job[0]) if job else "", job_status=str(job[1]) if job else "",
            payload_hash=str(job[2]) if job else "", payload_json=str(job[3]) if job else "{}",
            facts_json=str(job[4]) if job else "{}", validation_errors_json=str(job[5]) if job else "[]",
            uncertain_write=bool(job[6]) if job else False,
        ), ""

    def _facts(self, state: LocalDescriptionState) -> tuple[DescriptionFacts | None, str]:
        try:
            raw = dict(json.loads(state.facts_json))
            for name in ("fields", "operators", "windows", "groups"):
                raw[name] = tuple(raw[name])
            return DescriptionFacts(**raw), ""
        except (TypeError, KeyError, ValueError, json.JSONDecodeError):
            return None, "DESCRIPTION_FACTS_NOT_PERSISTED"

    def _payload(
        self, state: LocalDescriptionState, *, require_hash: bool = True
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            payload = json.loads(state.payload_json)
        except json.JSONDecodeError:
            return None, "DESCRIPTION_PAYLOAD_NOT_PERSISTED"
        if not isinstance(payload, dict):
            return None, "DESCRIPTION_PAYLOAD_EVIDENCE_INVALID"
        if require_hash and (
            not state.payload_hash or _hash(payload) != state.payload_hash
        ):
            return None, "DESCRIPTION_PAYLOAD_EVIDENCE_INVALID"
        return payload, ""

    def _validate_state(self, state: LocalDescriptionState) -> tuple[DescriptionValidation | None, str]:
        if not state.job_id:
            return None, "DESCRIPTION_JOB_NOT_FOUND"
        facts, reason = self._facts(state)
        if facts is None:
            return None, reason
        payload, reason = self._payload(state, require_hash=False)
        if payload is None:
            return None, reason
        text = _at_path(payload, state.schema.payload_path)
        if not isinstance(text, str):
            text = ""
        draft = DescriptionDraft(
            alpha_type=state.alpha_type,
            sections={name: "persisted" for name in state.schema.required_sections},
            text=text,
            payload=payload,
            source="persisted",
            facts_hash=facts.facts_hash,
            schema_hash=state.schema.schema_hash,
        )
        validation = validate_description(draft, facts, state.schema)
        if not state.payload_hash or _hash(payload) != state.payload_hash:
            validation = DescriptionValidation(
                False,
                tuple(dict.fromkeys([*validation.errors, "PAYLOAD_HASH_MISMATCH"])),
            )
        self._persist_validation(state, draft, validation)
        return validation, ""

    def _persist_validation(self, state: LocalDescriptionState, draft: DescriptionDraft, validation: DescriptionValidation) -> None:
        status = DescriptionStatus.VALIDATED if validation.valid else DescriptionStatus.FAILED
        with self._write_connection() as con:
            con.execute(
                """UPDATE description_backfill_jobs SET description_status=?,job_stage=?,
                   description_payload_hash=?,description_payload_json=?,validation_errors_json=?,
                   schema_hash=?,facts_hash=?,last_error=?,updated_at=? WHERE job_id=?""",
                (
                    status.value, status.value, _hash(draft.payload), _canonical(draft.payload),
                    _canonical(validation.errors), state.schema.schema_hash, draft.facts_hash,
                    "" if validation.valid else ";".join(validation.errors), _utc_now(), state.job_id,
                ),
            )

    def _set_job_state(
        self,
        state: LocalDescriptionState,
        status: DescriptionStatus,
        *,
        before_hash: str = "",
        after_hash: str = "",
        intent_id: str = "",
        uncertain: bool = False,
        increment_patch: bool = False,
        last_error: str = "",
    ) -> None:
        with self._write_connection() as con:
            con.execute(
                """UPDATE description_backfill_jobs SET description_status=?,job_stage=?,
                   platform_before_hash=?,platform_after_hash=?,patch_intent_id=?,
                   uncertain_write=?,patch_attempt_count=patch_attempt_count+?,last_error=?,
                   updated_at=?,completed_at=? WHERE job_id=?""",
                (
                    status.value, status.value, before_hash, after_hash, intent_id, int(uncertain),
                    int(increment_patch), last_error, _utc_now(),
                    _utc_now() if status is DescriptionStatus.VERIFIED else None, state.job_id,
                ),
            )

    def _factory_patch_permitted(self, sync_id: str) -> bool:
        try:
            with self._connection() as con:
                row = con.execute(
                    """SELECT hard_stop,ledger_sync_id,cluster_freeze_complete,execute_description_patch
                       FROM factory_control WHERE singleton=1"""
                ).fetchone()
        except sqlite3.Error:
            return False
        return bool(row) and not bool(row[0]) and str(row[1]) == sync_id and bool(row[2]) and bool(row[3])

    def _gateway(self) -> DescriptionGateway:
        if self.gateway_factory is not None:
            return self.gateway_factory()
        from alpha_mining.platform.gateway import PlatformGateway

        return PlatformGateway(database=self.database)

    def _connection(self) -> sqlite3.Connection:
        if not self.database.is_file():
            raise sqlite3.OperationalError("database is absent")
        return sqlite3.connect(f"file:{self.database.resolve().as_posix()}?mode=ro", uri=True)

    def _write_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)

    @staticmethod
    def _emit(command: str, status: str, **values: Any) -> None:
        print(json.dumps({"command": command, "status": status, **values}, sort_keys=True))

    def _blocked(self, command: str, reason: str) -> int:
        self._emit(command, "BLOCKED", reason=reason, platform_client_created=False)
        return 2
