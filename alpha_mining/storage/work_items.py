"""Authoritative candidate workflow state and CSV compatibility projection.

The generator and desktop client may both create candidates, but only this
store owns their workflow state.  ``待提交Alpha列表.csv`` remains a local
interoperability artifact and is always regenerated from SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .csv_queue import CandidateCsvQueue
from .migrations import backup_and_migrate, migrate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class WorkflowStatus(str, Enum):
    PENDING_SIMULATION = "PENDING_SIMULATION"
    SIMULATING = "SIMULATING"
    SIMULATION_UNCERTAIN = "SIMULATION_UNCERTAIN"
    WAITING_CHECKS = "WAITING_CHECKS"
    NEAR_PASS = "NEAR_PASS"
    FAR_FAIL = "FAR_FAIL"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    DESCRIPTION_VALIDATED = "DESCRIPTION_VALIDATED"
    AWAITING_BATCH_CONFIRMATION = "AWAITING_BATCH_CONFIRMATION"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    SUBMITTED = "SUBMITTED"
    REJECTED_LOCAL_REVALIDATION = "REJECTED_LOCAL_REVALIDATION"


_TERMINAL = frozenset({
    WorkflowStatus.FAR_FAIL.value,
    WorkflowStatus.SUBMITTED.value,
    WorkflowStatus.REJECTED_LOCAL_REVALIDATION.value,
})


@dataclass(frozen=True)
class CandidateWorkItem:
    candidate_id: str
    request_hash: str
    payload: dict[str, Any]
    source_evidence: dict[str, Any]
    state: str
    alpha_id: str
    metrics: dict[str, Any]
    checks: list[dict[str, Any]]
    quality_reasons: list[str]
    description_status: str
    submission_status: str
    parent_candidate_id: str
    tune_child_count: int
    last_error_category: str
    last_error: str
    created_at: str
    updated_at: str


class CandidateWorkStore:
    """SQLite single writer for candidate workflow state.

    State changes add an append-only event in the same transaction.  The
    public methods intentionally do not contain platform calls.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        migrate(self.database)

    def upsert_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        state: str = WorkflowStatus.PENDING_SIMULATION.value,
        source_evidence: Mapping[str, Any] | None = None,
        event_type: str = "ENQUEUED",
    ) -> bool:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        request_hash = str(candidate.get("request_hash") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if not request_hash:
            request_hash = hashlib.sha256(_json(dict(candidate)).encode("utf-8")).hexdigest()
        now = _now()
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT candidate_id,state FROM candidate_work_items WHERE candidate_id=? OR request_hash=?",
                (candidate_id, request_hash),
            ).fetchone()
            if existing:
                # Compatibility import is deliberately narrow: an older
                # single-writer CSV consumer may have advanced a row before
                # this process starts.  Once the CSV has been projected by
                # this store, callers must use ``transition`` instead.
                source_state = str(candidate.get("queue_status") or candidate.get("quality_status") or "").strip()
                source_updated = str(candidate.get("updated_at") or "")
                current = con.execute(
                    "SELECT state,updated_at FROM candidate_work_items WHERE candidate_id=?",
                    (str(existing[0]),),
                ).fetchone()
                if source_state and current and source_updated > str(current[1]):
                    con.execute(
                        """UPDATE candidate_work_items SET state=?,alpha_id=?,last_error_category=?,last_error=?,updated_at=?
                           WHERE candidate_id=?""",
                        (source_state, str(candidate.get("alpha_id") or ""), str(candidate.get("last_error_category") or ""),
                         str(candidate.get("last_error") or ""), source_updated, str(existing[0])),
                    )
                    self._event(con, str(existing[0]), "LEGACY_CSV_RECONCILED", str(current[0]), source_state, {})
                con.execute(
                    """UPDATE candidate_work_items SET payload_json=?,source_evidence_json=?,updated_at=?
                       WHERE candidate_id=?""",
                    (_json(dict(candidate)), _json(dict(source_evidence or {})), now, str(existing[0])),
                )
                self._event(con, str(existing[0]), "DEDUPLICATED", str(existing[1]), str(existing[1]), {"request_hash": request_hash})
                return False
            con.execute(
                """INSERT INTO candidate_work_items
                (candidate_id,request_hash,payload_json,source_evidence_json,state,alpha_id,last_error_category,last_error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, request_hash, _json(dict(candidate)), _json(dict(source_evidence or {})), state,
                 str(candidate.get("alpha_id") or ""), str(candidate.get("last_error_category") or ""),
                 str(candidate.get("last_error") or ""), now, now),
            )
            self._event(con, candidate_id, event_type, "", state, {"request_hash": request_hash})
        return True

    def list_items(self, *, states: Iterable[str] | None = None, limit: int | None = None) -> list[CandidateWorkItem]:
        clauses: list[str] = []
        args: list[Any] = []
        if states is not None:
            wanted = [str(value) for value in states]
            if not wanted:
                return []
            clauses.append("state IN (" + ",".join("?" for _ in wanted) + ")")
            args.extend(wanted)
        sql = "SELECT candidate_id,request_hash,payload_json,source_evidence_json,state,alpha_id,metrics_json,checks_json,quality_reasons_json,description_status,submission_status,parent_candidate_id,tune_child_count,last_error_category,last_error,created_at,updated_at FROM candidate_work_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at,candidate_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(max(0, int(limit)))
        with sqlite3.connect(self.database) as con:
            rows = con.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def get_item(self, candidate_id: str) -> CandidateWorkItem | None:
        values = self.list_items_for_ids([candidate_id])
        return values[0] if values else None

    def list_items_for_ids(self, candidate_ids: Iterable[str]) -> list[CandidateWorkItem]:
        ids = [str(value) for value in candidate_ids if str(value)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                "SELECT candidate_id,request_hash,payload_json,source_evidence_json,state,alpha_id,metrics_json,checks_json,quality_reasons_json,description_status,submission_status,parent_candidate_id,tune_child_count,last_error_category,last_error,created_at,updated_at FROM candidate_work_items WHERE candidate_id IN (" + placeholders + ")",
                ids,
            ).fetchall()
        return [self._row(row) for row in rows]

    def transition(
        self, candidate_id: str, state: str, *, event_type: str = "TRANSITION", details: Mapping[str, Any] | None = None,
        alpha_id: str | None = None, metrics: Mapping[str, Any] | None = None,
        checks: list[Mapping[str, Any]] | None = None, quality_reasons: Iterable[str] | None = None,
        error_category: str | None = None, error: str | None = None,
        description_status: str | None = None, submission_status: str | None = None,
    ) -> None:
        target = str(state)
        now = _now()
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT state FROM candidate_work_items WHERE candidate_id=?", (candidate_id,)).fetchone()
            if not row:
                raise KeyError(candidate_id)
            old = str(row[0])
            columns = ["state=?", "updated_at=?"]
            values: list[Any] = [target, now]
            for column, value in (("alpha_id", alpha_id), ("metrics_json", _json(dict(metrics)) if metrics is not None else None),
                                  ("checks_json", _json(list(checks)) if checks is not None else None),
                                  ("quality_reasons_json", _json(list(quality_reasons)) if quality_reasons is not None else None),
                                  ("last_error_category", error_category), ("last_error", error),
                                  ("description_status", description_status), ("submission_status", submission_status)):
                if value is not None:
                    columns.append(column + "=?")
                    values.append(value)
            values.append(candidate_id)
            con.execute("UPDATE candidate_work_items SET " + ",".join(columns) + " WHERE candidate_id=?", values)
            self._event(con, candidate_id, event_type, old, target, dict(details or {}))

    def create_tune_child(self, parent: CandidateWorkItem, settings: Mapping[str, Any], stage: str) -> CandidateWorkItem | None:
        if parent.tune_child_count >= 4:
            return None
        payload = dict(parent.payload)
        payload["settings"] = dict(settings)
        payload["parent_candidate_id"] = parent.candidate_id
        payload["tune_stage"] = str(stage)
        encoded = _json({"expression": payload.get("expression", ""), "settings": payload["settings"]})
        request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        candidate_id = hashlib.sha256((parent.candidate_id + "\0" + request_hash).encode("utf-8")).hexdigest()
        payload.update(candidate_id=candidate_id, request_hash=request_hash)
        if not self.upsert_candidate(payload, source_evidence=parent.source_evidence, event_type="TUNE_CHILD_CREATED"):
            return self.get_item(candidate_id)
        with sqlite3.connect(self.database) as con:
            con.execute("UPDATE candidate_work_items SET tune_child_count=tune_child_count+1,updated_at=? WHERE candidate_id=?", (_now(), parent.candidate_id))
        return self.get_item(candidate_id)

    def import_csv(self, csv_path: str | Path) -> int:
        """Idempotently import old queue rows exactly once by candidate/request hash."""
        queue = CandidateCsvQueue(Path(csv_path), Path(csv_path).with_suffix(".events.csv"))
        imported = 0
        for row in queue.read():
            candidate_id = str(row.get("candidate_id") or row.get("候选ID") or "").strip()
            if not candidate_id:
                continue
            row["candidate_id"] = candidate_id
            state = str(row.get("queue_status") or row.get("quality_status") or WorkflowStatus.PENDING_SIMULATION.value)
            if self.upsert_candidate(row, state=state, source_evidence={"source": "legacy_csv"}, event_type="CSV_IMPORTED"):
                imported += 1
        return imported

    def project_csv(self, csv_path: str | Path, events_path: str | Path | None = None) -> None:
        queue = CandidateCsvQueue(Path(csv_path), Path(events_path or Path(csv_path).with_suffix(".events.csv")))
        rows: list[dict[str, str]] = []
        for item in self.list_items():
            row = queue.empty_candidate()
            row.update({key: self._string(value) for key, value in item.payload.items() if key in row})
            row.update(
                candidate_id=item.candidate_id, request_hash=item.request_hash, queue_status=item.state,
                quality_status=item.state, alpha_id=item.alpha_id, metrics_json=_json(item.metrics), checks_json=_json(item.checks),
                quality_reasons_json=_json(item.quality_reasons), description_status=item.description_status,
                submission_status=item.submission_status, last_error_category=item.last_error_category,
                last_error=item.last_error, created_at=item.created_at, updated_at=item.updated_at,
            )
            rows.append(row)
        with queue.writer():
            queue.replace_all(rows)

    def create_batch_intent(self, candidate_ids: Iterable[str]) -> tuple[str, str]:
        items = self.list_items_for_ids(candidate_ids)
        if not items:
            raise ValueError("batch must contain candidates")
        ids = tuple(sorted(item.candidate_id for item in items))
        payload_hash = hashlib.sha256(_json([(item.candidate_id, item.alpha_id, item.description_status, item.updated_at) for item in items]).encode("utf-8")).hexdigest()
        batch_id = hashlib.sha256(("\0".join(ids) + "\0" + payload_hash).encode("utf-8")).hexdigest()
        with sqlite3.connect(self.database) as con:
            con.execute("INSERT OR IGNORE INTO candidate_batch_intents(batch_id,candidate_ids_json,payload_hash,status,created_at) VALUES (?,?,?,'PENDING',?)", (batch_id, _json(ids), payload_hash, _now()))
        return batch_id, payload_hash

    def confirm_batch(self, batch_id: str, payload_hash: str) -> tuple[str, ...]:
        with sqlite3.connect(self.database) as con:
            row = con.execute("SELECT candidate_ids_json,payload_hash,status FROM candidate_batch_intents WHERE batch_id=?", (batch_id,)).fetchone()
            if not row or str(row[1]) != payload_hash or str(row[2]) != "PENDING":
                raise ValueError("batch confirmation is stale or invalid")
            ids = tuple(json.loads(str(row[0])))
            current = self.list_items_for_ids(ids)
            actual = hashlib.sha256(_json([(item.candidate_id, item.alpha_id, item.description_status, item.updated_at) for item in current]).encode("utf-8")).hexdigest()
            if actual != payload_hash:
                con.execute("UPDATE candidate_batch_intents SET status='STALE',last_error=? WHERE batch_id=?", ("candidate collection changed", batch_id))
                raise ValueError("candidate collection changed; create a new batch")
            con.execute("UPDATE candidate_batch_intents SET status='CONFIRMED',confirmed_at=? WHERE batch_id=?", (_now(), batch_id))
        return ids

    def _event(self, con: sqlite3.Connection, candidate_id: str, event_type: str, old: str, new: str, details: Mapping[str, Any]) -> None:
        con.execute("INSERT INTO candidate_work_events(event_id,candidate_id,event_at,event_type,old_state,new_state,details_json) VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex, candidate_id, _now(), event_type, old, new, _json(dict(details))))

    @staticmethod
    def _string(value: Any) -> str:
        return value if isinstance(value, str) else _json(value)

    @staticmethod
    def _row(row: tuple[Any, ...]) -> CandidateWorkItem:
        def decoded(value: Any, default: Any) -> Any:
            try:
                return json.loads(str(value))
            except (TypeError, ValueError, json.JSONDecodeError):
                return default
        return CandidateWorkItem(str(row[0]), str(row[1]), decoded(row[2], {}), decoded(row[3], {}), str(row[4]), str(row[5]), decoded(row[6], {}), decoded(row[7], []), decoded(row[8], []), str(row[9]), str(row[10]), str(row[11]), int(row[12]), str(row[13]), str(row[14]), str(row[15]), str(row[16]))


def initialize_authoritative_database(canonical: str | Path, legacy: str | Path | None = None) -> Path:
    """Migrate the canonical DB only after refusing non-empty dual work ledgers."""
    target = Path(canonical)
    old = Path(legacy) if legacy is not None else None
    if old and old.resolve() != target.resolve() and old.is_file() and target.is_file():
        counts: dict[Path, int] = {}
        for path in (target, old):
            with sqlite3.connect(path) as con:
                tables = {str(value[0]) for value in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                watched = {"candidate_work_items", "simulation_requests", "candidate_outcomes", "consultant_submit_queue"} & tables
                counts[path] = sum(int(con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]) for table in watched)
        if counts.get(target, 0) and counts.get(old, 0):
            raise RuntimeError(
                f"refusing dual-ledger migration: canonical={counts[target]} legacy={counts[old]} workflow row(s)"
            )
    backup_and_migrate(target)
    return target
