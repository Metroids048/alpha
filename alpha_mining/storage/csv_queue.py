"""Single-writer CSV candidate queue with append-only status events."""

from __future__ import annotations

import csv
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


CANDIDATE_FIELDS = (
    "candidate_id", "request_hash", "created_at", "updated_at", "expression", "alpha_type",
    "region", "universe", "delay", "decay", "neutralization", "truncation",
    "language", "data_fields", "datasets", "operator_family", "exact_hash",
    "normalized_hash", "structure_signature", "behavior_signature", "canonical_signature",
    "generator_source", "parent_template", "parent_seed", "research_direction",
    "economic_hypothesis", "economic_rationale", "description_draft", "knowledge_refs_json",
    "feedback_refs_json", "anti_corr_design", "expected_turnover_behavior", "local_quality_score",
    "novelty_score", "self_corr_risk_score", "quality_evidence_json", "llm_model",
    "knowledge_usage_mode", "degraded", "local_score", "priority_score", "queue_status",
    "alpha_id", "retry_count", "last_error_category", "last_error", "field_skeleton",
)

EVENT_FIELDS = (
    "event_id", "candidate_id", "event_at", "event_type", "old_status", "new_status", "details",
)


class QueueLockedError(RuntimeError):
    pass


class CandidateCsvQueue:
    def __init__(
        self, queue_path: Path | str, events_path: Path | str,
        *, stale_lock_seconds: float = 3600,
    ) -> None:
        self.queue_path = Path(queue_path)
        self.events_path = Path(events_path)
        self.lock_path = self.queue_path.with_suffix(self.queue_path.suffix + ".lock")
        self.stale_lock_seconds = float(stale_lock_seconds)
        self._owns_lock = False

    @contextmanager
    def writer(self) -> Iterator["CandidateCsvQueue"]:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        try:
            yield self
        finally:
            if self._owns_lock:
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                self._owns_lock = False

    def empty_candidate(self) -> dict[str, str]:
        return {field: "" for field in CANDIDATE_FIELDS}

    def read(self) -> list[dict[str, str]]:
        return self._read_csv(self.queue_path)

    def read_events(self) -> list[dict[str, str]]:
        return self._read_csv(self.events_path)

    def upsert(self, candidate: dict[str, str]) -> bool:
        self._require_lock()
        row = self.empty_candidate()
        row.update({key: str(value) for key, value in candidate.items() if key in row})
        if not row["candidate_id"]:
            raise ValueError("candidate_id is required")
        now = _utc_now()
        row["created_at"] = row["created_at"] or now
        row["updated_at"] = now
        rows = self.read()
        previous = next(
            (
                item for item in rows
                if item["candidate_id"] == row["candidate_id"]
                or (row["request_hash"] and item.get("request_hash") == row["request_hash"])
            ),
            None,
        )
        if previous:
            row["created_at"] = previous["created_at"]
            # The consumer owns post-generation state.  A retry or a second
            # producer pass can enrich provenance, but may never rewind it.
            for field in ("queue_status", "alpha_id", "retry_count", "last_error_category", "last_error"):
                if previous.get(field):
                    row[field] = previous[field]
            row["candidate_id"] = previous["candidate_id"] or row["candidate_id"]
            rows = [row if item["candidate_id"] == row["candidate_id"] else item for item in rows]
        else:
            rows.append(row)
        self._atomic_write(rows)
        self._append_event(
            row["candidate_id"], previous["queue_status"] if previous else "",
            row["queue_status"], "DEDUPLICATED" if previous else "ENQUEUED",
            "candidate deduplicated; consumer state preserved" if previous else "candidate enqueued",
        )
        return previous is None

    def transition(self, candidate_id: str, new_status: str, details: str = "") -> None:
        self._require_lock()
        rows = self.read()
        for row in rows:
            if row["candidate_id"] == candidate_id:
                old_status = row["queue_status"]
                row["queue_status"] = new_status
                row["updated_at"] = _utc_now()
                self._atomic_write(rows)
                self._append_event(candidate_id, old_status, new_status, "TRANSITION", details)
                return
        raise KeyError(candidate_id)

    def replace_all(self, rows: list[dict[str, str]]) -> None:
        """Atomically replace the queue while preserving the declared CSV schema."""

        self._require_lock()
        normalized: list[dict[str, str]] = []
        for source in rows:
            row = self.empty_candidate()
            row.update({key: str(value) for key, value in source.items() if key in row})
            if not row["candidate_id"]:
                raise ValueError("candidate_id is required")
            normalized.append(row)
        self._atomic_write(normalized)

    def _acquire_lock(self) -> None:
        if self.lock_path.exists():
            age = time.time() - self.lock_path.stat().st_mtime
            if age <= self.stale_lock_seconds:
                raise QueueLockedError(f"候选队列正被其他写进程占用: {self.lock_path}")
            self.lock_path.unlink()
        payload = json.dumps({"pid": os.getpid(), "created_at": _utc_now()}, ensure_ascii=False)
        try:
            descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise QueueLockedError(f"候选队列正被其他写进程占用: {self.lock_path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self._owns_lock = True

    def _atomic_write(self, rows: list[dict[str, str]]) -> None:
        temporary = self.queue_path.with_name(self.queue_path.name + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.queue_path)

    def record_event(self, candidate_id: str, event_type: str, details: str = "") -> None:
        """Append an audit event without changing queue state."""
        self._require_lock()
        rows = self.read()
        current = next((item for item in rows if item["candidate_id"] == candidate_id), None)
        self._append_event(candidate_id, current["queue_status"] if current else "", current["queue_status"] if current else "", event_type, details)

    def _append_event(self, candidate_id: str, old: str, new: str, event_type: str, details: str) -> None:
        exists = self.events_path.exists() and self.events_path.stat().st_size > 0
        event_at = _utc_now()
        event_id = f"event_{time.time_ns()}_{os.getpid()}"
        with self.events_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerow(
                {"event_id": event_id, "candidate_id": candidate_id, "event_at": event_at,
                 "event_type": event_type, "old_status": old, "new_status": new, "details": details}
            )
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        if not path.is_file():
            return []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _require_lock(self) -> None:
        if not self._owns_lock:
            raise QueueLockedError("写候选队列前必须持有 lock")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
