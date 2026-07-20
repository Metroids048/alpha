"""Immutable guard-decision queue with submit-time fail-closed revalidation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from alpha_mining.policy.consultant_policy import ConsultantPolicy
from .guard import CandidateContext, GuardDecision, SubmissionGuard
from .judge import quality_buffer_pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SubmissionClient(Protocol):
    def submit(self, alpha_id: str) -> dict: ...


class ConsultantSubmitQueue:
    def __init__(
        self,
        database: str | Path,
        *,
        guard: SubmissionGuard | None = None,
        gate_freshness_hours: float = 24.0,
        policy: ConsultantPolicy | None = None,
    ) -> None:
        self.database = Path(database)
        self.guard = guard or SubmissionGuard()
        self.gate_freshness_hours = max(0.0, float(gate_freshness_hours))
        self.policy = policy or ConsultantPolicy(
            gate_freshness_hours=self.gate_freshness_hours
        )

    def enqueue(
        self, context: CandidateContext, payload: dict, gate_versions: dict[str, int]
    ) -> GuardDecision:
        decision = self.guard.evaluate(context)
        reasons = list(decision.reasons)
        with sqlite3.connect(self.database) as con:
            gates_current, gates = self._gate_snapshot_state(con, gate_versions)
        required = {name.upper() for name in context.mandatory_checks} | {
            "SELF_CORRELATION"
        }
        check_names = {str(check.get("name") or "").upper() for check in context.checks}
        required |= check_names & {
            "PROD_CORRELATION",
            "PRODUCTION_CORRELATION",
            "LOW_SUB_UNIVERSE_SHARPE",
        }
        if not gates_current or not required <= set(gates):
            reasons.append("GATE_SNAPSHOT_VERSIONS_MISSING")
        dynamic_quality, _ = quality_buffer_pass(
            context.metrics, gates, policy=self.policy
        )
        if not dynamic_quality:
            reasons.append("DYNAMIC_QUALITY_BUFFER_FAILED")
        decision = GuardDecision(not reasons, tuple(dict.fromkeys(reasons)))
        payload_hash = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        queue_id = hashlib.sha256(
            f"{context.expression_id}\0{payload_hash}".encode()
        ).hexdigest()
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT INTO consultant_submit_queue
                (queue_id,expression_id,alpha_id,payload_hash,status,reasons_json,gate_versions_json,
                 created_at,updated_at,payload_json,context_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    status=excluded.status,reasons_json=excluded.reasons_json,
                    gate_versions_json=excluded.gate_versions_json,updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json,context_json=excluded.context_json""",
                (
                    queue_id,
                    context.expression_id,
                    context.alpha_id,
                    payload_hash,
                    "READY" if decision.allowed else "BLOCKED",
                    json.dumps(decision.reasons),
                    json.dumps(gate_versions, sort_keys=True),
                    now,
                    now,
                    json.dumps(payload, sort_keys=True),
                    json.dumps(asdict(context), sort_keys=True),
                ),
            )
        return decision

    def _gate_snapshot_state(
        self,
        con: sqlite3.Connection,
        versions: dict[str, int],
    ) -> tuple[bool, dict[str, tuple[float, str]]]:
        if not versions:
            return False, {}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.gate_freshness_hours)
        gates: dict[str, tuple[float, str]] = {}
        for snapshot_key, expected_version in versions.items():
            row = con.execute(
                "SELECT version,last_seen_at,gate_name,limit_value,direction FROM platform_gate_snapshots WHERE snapshot_key=?",
                (str(snapshot_key),),
            ).fetchone()
            if row is None or int(row[0]) != int(expected_version):
                return False, {}
            try:
                if _parse_time(str(row[1])) < cutoff:
                    return False, {}
            except (TypeError, ValueError):
                return False, {}
            gates[str(row[2]).upper()] = (float(row[3]), str(row[4]))
        return True, gates

    def execute_ready(
        self,
        client: SubmissionClient,
        *,
        max_items: int = 20,
        execute: bool = False,
    ) -> dict[str, int]:
        """Submit only after an explicit execute flag and fresh, unchanged gates."""
        counts = {"submitted": 0, "blocked": 0, "failed": 0, "execution_disabled": 0}
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT queue_id,alpha_id,payload_hash,payload_json,context_json,gate_versions_json
                FROM consultant_submit_queue WHERE status='READY' ORDER BY created_at,queue_id LIMIT ?""",
                (max(0, int(max_items)),),
            ).fetchall()
            if not execute:
                counts["execution_disabled"] = len(rows)
                return counts

            already_submitted = {
                str(row[0])
                for row in con.execute(
                    "SELECT alpha_id FROM consultant_submit_queue WHERE status IN ('PROCESSING','SUBMITTED')"
                )
                if str(row[0]).strip()
            }
            for (
                queue_id,
                alpha_id,
                payload_hash,
                payload_json,
                context_json,
                versions_json,
            ) in rows:
                payload = json.loads(payload_json)
                raw = json.loads(context_json)
                raw["unit_warnings"] = tuple(raw.get("unit_warnings") or ())
                raw["mandatory_checks"] = tuple(raw.get("mandatory_checks") or ())
                decision = self.guard.evaluate(CandidateContext(**raw))
                current_hash = hashlib.sha256(
                    json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), default=str
                    ).encode()
                ).hexdigest()
                reasons = list(decision.reasons)
                if current_hash != payload_hash:
                    reasons.append("PAYLOAD_HASH_CHANGED")
                try:
                    gate_versions = json.loads(versions_json or "{}")
                except (TypeError, ValueError):
                    gate_versions = {}
                gates_current, gates = self._gate_snapshot_state(con, gate_versions)
                required = {
                    name.upper() for name in raw.get("mandatory_checks") or ()
                } | {"SELF_CORRELATION"}
                check_names = {
                    str(check.get("name") or "").upper()
                    for check in raw.get("checks") or ()
                }
                required |= check_names & {
                    "PROD_CORRELATION",
                    "PRODUCTION_CORRELATION",
                    "LOW_SUB_UNIVERSE_SHARPE",
                }
                if not gates_current or not required <= set(gates):
                    reasons.append("GATE_SNAPSHOT_STALE_OR_CHANGED")
                dynamic_quality, _ = quality_buffer_pass(
                    raw.get("metrics") or {}, gates, policy=self.policy
                )
                if not dynamic_quality:
                    reasons.append("DYNAMIC_QUALITY_BUFFER_FAILED")
                normalized_alpha_id = str(alpha_id).strip()
                if not normalized_alpha_id:
                    reasons.append("ALPHA_ID_MISSING")
                elif normalized_alpha_id in already_submitted:
                    reasons.append("ALPHA_ALREADY_SUBMITTED_OR_PROCESSING")
                if reasons:
                    con.execute(
                        "UPDATE consultant_submit_queue SET status='BLOCKED',reasons_json=?,updated_at=? WHERE queue_id=?",
                        (
                            json.dumps(tuple(dict.fromkeys(reasons))),
                            _utc_now(),
                            queue_id,
                        ),
                    )
                    counts["blocked"] += 1
                    continue

                con.execute(
                    "UPDATE consultant_submit_queue SET status='PROCESSING',execute_requested=1,updated_at=? WHERE queue_id=? AND status='READY'",
                    (_utc_now(), queue_id),
                )
                con.commit()
                already_submitted.add(normalized_alpha_id)
                try:
                    response = client.submit(normalized_alpha_id)
                    ok = (
                        bool(response.get("ok"))
                        if isinstance(response, dict)
                        else False
                    )
                except Exception:
                    ok = False
                con.execute(
                    "UPDATE consultant_submit_queue SET status=?,updated_at=? WHERE queue_id=?",
                    ("SUBMITTED" if ok else "FAILED", _utc_now(), queue_id),
                )
                con.commit()
                counts["submitted" if ok else "failed"] += 1
        return counts
