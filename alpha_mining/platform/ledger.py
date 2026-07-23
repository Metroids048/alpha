"""Authoritative, append-only platform Alpha ledger synchronization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.storage.migrations import migrate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AlphaQueryFilters:
    status: str = "UNSUBMITTED"
    region: str | None = None
    universe: str | None = None
    delay: int | None = None
    hidden: bool | None = None
    alpha_type: str | None = None
    date_created_gte: str | None = None
    date_created_lt: str | None = None

    def to_params(self) -> dict[str, object]:
        values: dict[str, object] = {"status": self.status}
        optional = {
            "settings.region": self.region,
            "settings.universe": self.universe,
            "settings.delay": self.delay,
            "hidden": self.hidden,
            "type": self.alpha_type,
            "dateCreated>=": self.date_created_gte,
            "dateCreated<": self.date_created_lt,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        return values


class AlphaListClient(Protocol):
    def list_alphas(self, params: dict[str, object]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LedgerSyncResult:
    sync_id: str
    status: str
    declared_count: int
    fetched_rows: int
    unique_alpha_ids: int
    duplicate_alpha_ids: int
    filters: AlphaQueryFilters
    synced_at: str


def _expression(payload: dict[str, Any]) -> str:
    for key in ("regular", "selection", "combo"):
        value = payload.get(key)
        if isinstance(value, dict):
            for code_key in ("code", key, "expression"):
                if value.get(code_key):
                    return str(value[code_key])
        elif isinstance(value, str) and value.strip():
            return value
    return str(payload.get("expression") or "")


def _description(payload: dict[str, Any], alpha_type: str) -> str:
    value = payload.get(alpha_type.lower())
    return str(value.get("description") or "") if isinstance(value, dict) else ""


class PlatformLedgerSynchronizer:
    def __init__(self, database: str | Path, *, page_size: int = 100, max_offset: int = 9900) -> None:
        self.database = Path(database)
        self.page_size = max(1, min(100, int(page_size)))
        self.max_offset = max(0, int(max_offset))

    def sync(self, client: AlphaListClient, filters: AlphaQueryFilters) -> LedgerSyncResult:
        migrate(self.database)
        synced_at = _utc_now()
        filter_json = _canonical(filters.to_params())
        sync_id = hashlib.sha256(f"{synced_at}\0{filter_json}".encode()).hexdigest()[:24]
        set_sync_id = getattr(client, "set_sync_id", None)
        if callable(set_sync_id):
            set_sync_id(sync_id)
        rows: list[dict[str, Any]] = []
        page_manifest: list[dict[str, Any]] = []
        declared = 0
        page_number = 0
        error = ""

        def request(segment: AlphaQueryFilters, offset: int) -> tuple[int, list[dict[str, Any]]] | None:
            nonlocal page_number, error
            params = {**segment.to_params(), "limit": self.page_size, "offset": offset, "order": "-dateCreated"}
            try:
                payload = client.list_alphas(params)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                page_manifest.append({"page": page_number, "offset": offset, "filters": segment.to_params(), "declared": 0, "results": 0, "hash": "", "status": "ERROR", "error": error})
                page_number += 1
                return None
            if not isinstance(payload, dict):
                error = "list response is not an object"
                return None
            try:
                segment_count = int(payload.get("count") or 0)
            except (TypeError, ValueError):
                error = "invalid count"
                return None
            batch = [item for item in (payload.get("results") or []) if isinstance(item, dict)]
            page_manifest.append({"page": page_number, "offset": offset, "filters": segment.to_params(), "declared": segment_count, "results": len(batch), "hash": _hash(payload), "status": "OK", "error": ""})
            page_number += 1
            return segment_count, batch

        capacity = self.max_offset + self.page_size

        def parse_boundary(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        def format_boundary(value: datetime) -> str:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        def fetch_segment(segment: AlphaQueryFilters, expected_count: int | None = None) -> tuple[list[dict[str, Any]], int]:
            nonlocal error
            first = request(segment, 0)
            if first is None:
                return [], 0
            segment_count, first_batch = first
            if expected_count is not None and segment_count != expected_count:
                error = f"count changed for identical filters: {expected_count} -> {segment_count}"
                return [], segment_count
            if segment_count > capacity:
                start_text = segment.date_created_gte or "1900-01-01T00:00:00Z"
                end_text = segment.date_created_lt or format_boundary(datetime.now(timezone.utc) + timedelta(days=1))
                try:
                    start = parse_boundary(start_text)
                    end = parse_boundary(end_text)
                except ValueError:
                    error = "invalid date shard boundary"
                    return [], segment_count
                midpoint = start + (end - start) / 2
                if midpoint <= start or midpoint >= end or (end - start).total_seconds() <= 1:
                    error = "incomplete pagination: date shard cannot be split further"
                    return [], segment_count
                midpoint_text = format_boundary(midpoint)
                left = replace(segment, date_created_gte=start_text, date_created_lt=midpoint_text)
                right = replace(segment, date_created_gte=midpoint_text, date_created_lt=end_text)
                left_rows, left_count = fetch_segment(left)
                if error:
                    return [], segment_count
                right_rows, right_count = fetch_segment(right)
                if error:
                    return [], segment_count
                if left_count + right_count != segment_count:
                    error = f"date filters ineffective or non-half-open: parent={segment_count} children={left_count + right_count}"
                    return [], segment_count
                return [*left_rows, *right_rows], segment_count

            segment_rows = list(first_batch)
            offset = len(first_batch)
            while offset < segment_count:
                if offset > self.max_offset:
                    error = f"incomplete pagination: offset {offset} exceeds limit {self.max_offset}"
                    return segment_rows, segment_count
                page = request(segment, offset)
                if page is None:
                    return segment_rows, segment_count
                repeated_count, batch = page
                if repeated_count != segment_count:
                    error = f"count changed within segment: {segment_count} -> {repeated_count}"
                    return segment_rows, segment_count
                if not batch:
                    error = f"incomplete pagination: empty page at offset {offset} of {segment_count}"
                    return segment_rows, segment_count
                segment_rows.extend(batch)
                offset += len(batch)
            return segment_rows, segment_count

        root_page = request(filters, 0)
        if root_page is not None:
            declared, root_batch = root_page
            if declared > capacity:
                rows, _ = fetch_segment(filters, expected_count=declared)
            else:
                rows = list(root_batch)
                offset = len(root_batch)
                while offset < declared and not error:
                    if offset > self.max_offset:
                        error = f"incomplete pagination: offset {offset} exceeds limit {self.max_offset}"
                        break
                    page = request(filters, offset)
                    if page is None:
                        break
                    repeated_count, batch = page
                    if repeated_count != declared:
                        error = f"count changed within root query: {declared} -> {repeated_count}"
                        break
                    if not batch:
                        error = f"incomplete pagination: empty page at offset {offset} of {declared}"
                        break
                    rows.extend(batch)
                    offset += len(batch)

        seen: dict[str, dict[str, Any]] = {}
        duplicates = 0
        for row in rows:
            alpha_id = str(row.get("id") or row.get("alphaId") or "").strip()
            if not alpha_id:
                continue
            if alpha_id in seen:
                duplicates += 1
            seen[alpha_id] = row
        status = "PARTIAL" if error else "COMPLETE" if len(seen) == declared else "MISMATCH"
        with sqlite3.connect(self.database) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute(
                """INSERT INTO platform_sync_runs
                (sync_id,filters_json,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,status,error_message,started_at,completed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sync_id, filter_json, declared, len(rows), len(seen), duplicates, status, error, synced_at, synced_at),
            )
            for page in page_manifest:
                con.execute(
                    """INSERT INTO platform_sync_pages
                    (sync_id,page_number,offset_value,filters_json,declared_count,result_count,response_hash,status,error_message)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sync_id, page["page"], page["offset"], _canonical(page["filters"]), page["declared"], page["results"], page["hash"], page["status"], page["error"]),
                )
            for alpha_id, raw in seen.items():
                raw_hash = _hash(raw)
                con.execute(
                    """INSERT OR IGNORE INTO platform_alpha_observations
                    (sync_id,alpha_id,raw_payload_hash,raw_payload_json,synced_at) VALUES (?,?,?,?,?)""",
                    (sync_id, alpha_id, raw_hash, _canonical(raw), synced_at),
                )
            if status == "COMPLETE":
                for alpha_id, raw in seen.items():
                    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
                    is_metrics = raw.get("is") if isinstance(raw.get("is"), dict) else {}
                    alpha_type = str(raw.get("type") or "REGULAR").upper()
                    expression = _expression(raw)
                    checks = is_metrics.get("checks") if isinstance(is_metrics.get("checks"), list) else []
                    con.execute(
                        """INSERT INTO platform_alpha_ledger
                        (alpha_id,sync_id,platform_status,alpha_type,hidden,date_created,date_modified,
                         region,universe_name,delay,expression_hash,settings_hash,is_metrics_json,
                         latest_checks_json,regular_description,selection_description,combo_description,
                         synced_at,raw_payload_hash)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(alpha_id) DO UPDATE SET
                         sync_id=excluded.sync_id,platform_status=excluded.platform_status,
                         alpha_type=excluded.alpha_type,hidden=excluded.hidden,date_created=excluded.date_created,
                         date_modified=excluded.date_modified,region=excluded.region,universe_name=excluded.universe_name,
                         delay=excluded.delay,expression_hash=excluded.expression_hash,settings_hash=excluded.settings_hash,
                         is_metrics_json=excluded.is_metrics_json,latest_checks_json=excluded.latest_checks_json,
                         regular_description=excluded.regular_description,selection_description=excluded.selection_description,
                         combo_description=excluded.combo_description,synced_at=excluded.synced_at,
                         raw_payload_hash=excluded.raw_payload_hash""",
                        (
                            alpha_id, sync_id, str(raw.get("status") or "UNKNOWN").upper(), alpha_type,
                            int(bool(raw.get("hidden"))), str(raw.get("dateCreated") or ""),
                            str(raw.get("dateModified") or ""), str(settings.get("region") or ""),
                            str(settings.get("universe") or ""), str(settings.get("delay") if settings.get("delay") is not None else ""),
                            _hash(expression), _hash(settings), _canonical(is_metrics), _canonical(checks),
                            _description(raw, "REGULAR"), _description(raw, "SELECTION"), _description(raw, "COMBO"),
                            synced_at, _hash(raw),
                        ),
                    )
                con.execute(
                    "UPDATE factory_control SET ledger_sync_id=?,cluster_freeze_complete=0,"
                    "readiness_state='cluster_freeze_required',readiness_reason='cluster_freeze_required',"
                    "reason='cluster_freeze_required',execute_submit=0,updated_at=? WHERE singleton=1",
                    (sync_id, synced_at),
                )
        return LedgerSyncResult(sync_id, status, declared, len(rows), len(seen), duplicates, filters, synced_at)
