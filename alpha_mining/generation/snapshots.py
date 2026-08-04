"""Read-only local inputs for the pure generation chain.

The loader intentionally has a small, explicit search order.  It never calls a
platform client and never promotes arbitrary test/export artifacts to a live
catalog.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from alpha_mining.generation.feedback import CandidateFeedbackStore
from alpha_mining.offline.metadata import MetadataCache, MetadataCacheError, MetadataCacheMissing, MetadataCacheStale


class CatalogUnavailable(RuntimeError):
    """The complete local datasets/fields/operators snapshot is unavailable."""


@dataclass(frozen=True)
class FeedbackRecord:
    ref_id: str
    request_hash: str
    expression: str
    outcome: str
    family: str
    dataset: str
    failure_types: tuple[str, ...]
    self_corr_risk: bool
    field_skeleton: str = ""


@dataclass(frozen=True)
class FeedbackSummary:
    records: tuple[FeedbackRecord, ...]
    positive: tuple[FeedbackRecord, ...]
    near_pass: tuple[FeedbackRecord, ...]
    failures: tuple[FeedbackRecord, ...]
    self_corr_risk: tuple[FeedbackRecord, ...]
    failure_counts: dict[str, int]

    @property
    def expressions(self) -> tuple[str, ...]:
        return tuple(item.expression for item in self.records if item.expression)


@dataclass(frozen=True)
class LocalSnapshots:
    catalog: MetadataCache
    catalog_dir: Path
    catalog_source: str
    catalog_age_hours: float
    feedback: FeedbackSummary


def load_catalog_snapshot(
    *,
    root: Path | str = ".",
    catalog_dir: Path | str | None = None,
    allow_stale: bool = True,
) -> tuple[MetadataCache, Path, str, float]:
    """Load a complete catalog from an explicit, finite list of locations."""

    root_path = Path(root)
    candidates: list[tuple[Path, str, str]] = []
    if catalog_dir is not None:
        candidates.append((Path(catalog_dir), "explicit", "auto"))
    candidates.extend(
        (
            (root_path, "root-dot-cache", "dot"),
            (root_path / "数据" / "平台缓存", "chinese-platform-cache", "full4"),
        )
    )
    errors: list[str] = []
    for path, source, protocol in candidates:
        try:
            if protocol == "auto":
                try:
                    metadata = MetadataCache.from_platform_disk_cache(path, allow_stale=allow_stale)
                except (MetadataCacheMissing, MetadataCacheStale, MetadataCacheError):
                    metadata = MetadataCache.load(path, allow_stale=allow_stale)
            elif protocol == "full4":
                metadata = MetadataCache.load(path, allow_stale=allow_stale)
            else:
                metadata = MetadataCache.from_platform_disk_cache(path, allow_stale=allow_stale)
            return metadata, path, source, _age_hours(metadata.info)
        except (MetadataCacheMissing, MetadataCacheStale, MetadataCacheError) as exc:
            errors.append(f"{source}:{type(exc).__name__}:{str(exc)[:180]}")
    raise CatalogUnavailable("CATALOG_UNAVAILABLE; " + " | ".join(errors))


def load_local_snapshots(
    *,
    root: Path | str = ".",
    catalog_dir: Path | str | None = None,
    database: Path | str | None = None,
    queue_path: Path | str | None = None,
) -> LocalSnapshots:
    root_path = Path(root)
    catalog, source_dir, source, age = load_catalog_snapshot(root=root_path, catalog_dir=catalog_dir)
    db_path = Path(database) if database is not None else root_path / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
    queue = Path(queue_path) if queue_path is not None else root_path / "待提交Alpha列表.csv"
    feedback = load_feedback_summary(db_path, queue_path=queue, root=root_path)
    return LocalSnapshots(catalog, source_dir, source, age, feedback)


def load_feedback_summary(
    database: Path | str,
    *,
    queue_path: Path | str | None = None,
    root: Path | str = ".",
) -> FeedbackSummary:
    """Combine SQLite outcomes, the producer queue, and known v50 CSV history."""

    db = Path(database)
    records: list[FeedbackRecord] = []
    if db.exists():
        try:
            CandidateFeedbackStore(db)
            with sqlite3.connect(db) as con:
                columns = {row[1] for row in con.execute("PRAGMA table_info(candidate_outcomes)")}
                wanted = [
                    "request_hash", "outcome", "strategy_family", "dataset", "field_skeleton",
                    "checks_json", "error_category", "self_correlation", "prod_correlation",
                ]
                if "candidate_outcomes" in _tables(con):
                    query = "SELECT " + ",".join(name if name in columns else "''" for name in wanted) + " FROM candidate_outcomes"
                    for row in con.execute(query):
                        request_hash, outcome, family, dataset, field_skeleton, checks_json, error_category, self_corr, prod_corr = row
                        failures = _failure_types(checks_json, error_category, self_corr, prod_corr, outcome)
                        records.append(
                            FeedbackRecord(
                                _stable_ref("sqlite", str(request_hash)), str(request_hash), "", str(outcome or ""),
                                str(family or ""), str(dataset or ""), tuple(failures),
                                "SELF_CORRELATION" in failures or str(self_corr or "").upper() in {"FAIL", "FAILED"},
                                str(field_skeleton or ""),
                            )
                        )
        except sqlite3.Error:
            pass
    for row in _read_queue(queue_path):
        failures = _failure_types(row.get("quality_evidence_json"), row.get("last_error_category"), row.get("self_corr_risk_score"), "", row.get("queue_status"))
        request_hash = str(row.get("request_hash") or row.get("candidate_id") or "")
        records.append(
            FeedbackRecord(
                _stable_ref("queue", request_hash), request_hash, str(row.get("expression") or ""),
                str(row.get("queue_status") or ""), str(row.get("operator_family") or ""),
                str(row.get("datasets") or ""), tuple(failures),
                "SELF_CORRELATION" in failures or float(_number(row.get("self_corr_risk_score"))) >= 0.65,
                str(row.get("field_skeleton") or ""),
            )
        )
    for path in _history_csv_paths(Path(root)):
        records.extend(_read_history_csv(path))
    unique = {item.ref_id: item for item in records}
    records = sorted(unique.values(), key=lambda item: item.ref_id)
    positive = tuple(item for item in records if item.outcome.upper() in {"PASS", "READY_TO_SUBMIT", "SIMULATED"} and not item.failure_types)
    near_pass = tuple(item for item in records if item.outcome.upper() == "NEAR_PASS" or "NEAR_PASS" in item.failure_types)
    failures = tuple(item for item in records if item not in positive and item not in near_pass)
    risk = tuple(item for item in records if item.self_corr_risk)
    counts: dict[str, int] = {}
    for item in records:
        for failure in item.failure_types:
            counts[failure] = counts.get(failure, 0) + 1
    return FeedbackSummary(tuple(records), positive, near_pass, failures, risk, counts)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _read_queue(path: Path | str | None) -> list[dict[str, str]]:
    if path is None or not Path(path).is_file():
        return []
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def _history_csv_paths(root: Path) -> list[Path]:
    patterns = ("alpha_submission_feedback*.csv", "worldquant_alphas_repo_feedback.csv", "*_results.csv", "*_checkpoint.csv")
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(root.glob(pattern))
    return sorted({path for path in paths if path.is_file()}, key=lambda item: item.as_posix())


def _read_history_csv(path: Path) -> list[FeedbackRecord]:
    result: list[FeedbackRecord] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                expression = str(row.get("expression") or "").strip()
                if not expression:
                    continue
                request_hash = str(row.get("request_hash") or hashlib.sha256(expression.encode()).hexdigest())
                failures = _failure_types(row.get("platform_check_json") or row.get("Failure Reasons"), row.get("blocked_reason"), row.get("self_correlation_status"), row.get("prod_correlation"), row.get("status"))
                result.append(FeedbackRecord(
                    _stable_ref(path.name, f"{index}:{request_hash}"), request_hash, expression,
                    str(row.get("status") or ""), str(row.get("family") or ""), str(row.get("dataset") or ""),
                    tuple(failures), "SELF_CORRELATION" in failures, str(row.get("field_skeleton") or ""),
                ))
    except (OSError, csv.Error):
        return []
    return result


def _failure_types(*values: object) -> list[str]:
    text = " ".join(str(value or "") for value in values).upper()
    known = ("SELF_CORRELATION", "PROD_CORRELATION", "LOW_SHARPE", "LOW_FITNESS", "HIGH_TURNOVER", "CONCENTRATED_WEIGHT", "NEAR_PASS")
    return [item for item in known if item in text]


def _stable_ref(namespace: str, value: str) -> str:
    return f"feedback:{namespace}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _age_hours(info: dict[str, Any]) -> float:
    value = str(info.get("fetched_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
    except (TypeError, ValueError):
        cached_at = float(info.get("cached_at") or time.time())
        return max(0.0, (time.time() - cached_at) / 3600)


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
