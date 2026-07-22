"""Sanitized platform access and ledger reports."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha_mining.storage.migrations import migrate


EVENT_HEADERS = [
    "timestamp", "endpoint_class", "method", "status_code", "retry_after_seconds",
    "retry_after_until", "auth_session_id", "process_id", "request_id", "attempt",
    "backoff_seconds", "response_hash", "error_class", "sync_id",
]


def export_request_events(database: str | Path, output_path: str | Path) -> int:
    migrate(database)
    with sqlite3.connect(database) as con:
        rows = con.execute(
            "SELECT timestamp,endpoint_class,method,status_code,retry_after_seconds,retry_after_until,"
            "auth_session_id,process_id,request_id,attempt,backoff_seconds,response_hash,error_class,sync_id "
            "FROM platform_request_events ORDER BY timestamp,event_id"
        ).fetchall()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(EVENT_HEADERS)
        writer.writerows(rows)
    return len(rows)


def write_ledger_sync_report(database: str | Path, output_path: str | Path) -> dict[str, Any]:
    migrate(database)
    with sqlite3.connect(database) as con:
        sync = con.execute(
            "SELECT sync_id,status,declared_count,fetched_rows,unique_alpha_ids,duplicate_alpha_ids,"
            "started_at,completed_at,error_message FROM platform_sync_runs ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        access = con.execute(
            "SELECT state,last_401,last_403,last_429,retry_after_until FROM platform_access_state WHERE singleton=1"
        ).fetchone()
        if sync:
            sync_id = str(sync[0])
            pages = int(con.execute("SELECT COUNT(*) FROM platform_sync_pages WHERE sync_id=?", (sync_id,)).fetchone()[0])
            shards = int(con.execute("SELECT COUNT(DISTINCT filters_json) FROM platform_sync_pages WHERE sync_id=?", (sync_id,)).fetchone()[0])
            ledger_rows = int(con.execute("SELECT COUNT(*) FROM platform_alpha_ledger WHERE sync_id=?", (sync_id,)).fetchone()[0])
            event_rows = con.execute(
                "SELECT status_code,retry_after_seconds FROM platform_request_events WHERE sync_id=?",
                (sync_id,),
            ).fetchall()
        else:
            sync_id, pages, shards, ledger_rows, event_rows = "", 0, 0, 0, []
    status_counts = Counter(int(row[0]) for row in event_rows)
    retry_distribution = Counter(float(row[1]) for row in event_rows if float(row[1] or 0) > 0)
    payload: dict[str, Any] = {
        "platform_count": int(sync[2]) if sync else 0,
        "unique_alpha_ids": int(sync[4]) if sync else 0,
        "fetched_rows": int(sync[3]) if sync else 0,
        "pages": pages,
        "shards": shards,
        "duplicates": int(sync[5]) if sync else 0,
        "missing": max(0, int(sync[2]) - int(sync[4])) if sync else 0,
        "http_401": status_counts.get(401, 0),
        "http_403": status_counts.get(403, 0),
        "http_429": status_counts.get(429, 0),
        "retry_after_distribution": {str(key): value for key, value in sorted(retry_distribution.items())},
        "ledger_rows": ledger_rows,
        "ledger_status": str(sync[1]) if sync else "MISSING",
        "sync_id": sync_id,
        "started_at": sync[6] if sync else None,
        "completed_at": sync[7] if sync else None,
        "error_class": str(sync[8]).split(":", 1)[0] if sync and sync[8] else "",
        "circuit_state": str(access[0]) if access else "MISSING",
        "last_401": access[1] if access else None,
        "last_403": access[2] if access else None,
        "last_429": access[3] if access else None,
        "retry_after_until": access[4] if access else None,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)
    return payload
