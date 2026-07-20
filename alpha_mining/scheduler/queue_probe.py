"""Read-only probes for hopeful / submission JSONL queues (used by run_pipeline_loop)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alpha_mining.common import to_float

SUBMITTED_STATUSES = frozenset({"submitted", "already_submitted", "dry_run"})


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _hopeful_latest_by_alpha(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_idx: dict[str, int] = {}
    for i, obj in enumerate(rows):
        aid = str(obj.get("alpha_id") or "").strip()
        if aid:
            last_idx[aid] = i
    out: list[dict[str, Any]] = []
    for i, obj in enumerate(rows):
        aid = str(obj.get("alpha_id") or "").strip()
        if aid and last_idx.get(aid) != i:
            continue
        out.append(obj)
    return out


def submitted_alpha_ids(submission_path: Path) -> set[str]:
    out: set[str] = set()
    for obj in _load_jsonl(submission_path):
        if str(obj.get("status") or "") not in SUBMITTED_STATUSES:
            continue
        aid = str(obj.get("alpha_id") or "").strip()
        if aid:
            out.add(aid)
    return out


def is_submit_eligible(
    row: dict[str, Any],
    submitted_ids: set[str],
    *,
    max_queue_similarity: float = 0.72,
) -> bool:
    """Fail closed on platform checks; numeric platform gates are never guessed locally."""
    alpha_id = str(row.get("alpha_id") or "").strip()
    if not alpha_id or alpha_id in submitted_ids:
        return False
    if str(row.get("status") or "ready").lower() != "ready":
        return False
    checks = row.get("checks") if isinstance(row.get("checks"), list) else []
    if not checks or row.get("check_passed") is not True:
        return False
    statuses = {
        str(c.get("name") or "").upper(): str(
            c.get("result") or c.get("status") or "UNKNOWN"
        ).upper()
        for c in checks
        if isinstance(c, dict)
    }
    if not statuses or any(status != "PASS" for status in statuses.values()):
        return False
    if statuses.get("SELF_CORRELATION", "MISSING") != "PASS":
        return False
    similarity = to_float(row.get("similarity_to_winners")) or 0.0
    if similarity >= max_queue_similarity:
        return False
    return True


def count_ready_to_submit(
    hopeful_path: Path | str,
    submission_path: Path | str,
    *,
    max_queue_similarity: float = 0.72,
) -> int:
    """Count hopeful rows that ``run_submit_queue`` would still try to submit."""
    hopeful_path = Path(hopeful_path)
    submission_path = Path(submission_path)
    submitted = submitted_alpha_ids(submission_path)
    hopeful = _hopeful_latest_by_alpha(_load_jsonl(hopeful_path))
    n = 0
    for row in hopeful:
        if is_submit_eligible(
            row,
            submitted,
            max_queue_similarity=max_queue_similarity,
        ):
            n += 1
    return n


def list_ready_to_submit(
    hopeful_path: Path | str,
    submission_path: Path | str,
    *,
    max_queue_similarity: float = 0.72,
) -> list[dict[str, Any]]:
    hopeful_path = Path(hopeful_path)
    submission_path = Path(submission_path)
    submitted = submitted_alpha_ids(submission_path)
    hopeful = _hopeful_latest_by_alpha(_load_jsonl(hopeful_path))
    out: list[dict[str, Any]] = []
    for row in hopeful:
        if is_submit_eligible(
            row,
            submitted,
            max_queue_similarity=max_queue_similarity,
        ):
            out.append(row)
    return out
