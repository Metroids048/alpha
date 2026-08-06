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


DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS = 336.0


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
    grounded: bool = True


@dataclass(frozen=True)
class InventoryRecord:
    ref_id: str
    candidate_id: str
    request_hash: str
    expression: str
    queue_status: str
    family: str
    dataset: str
    data_fields: tuple[str, ...] = ()
    research_direction: str = ""
    last_error_category: str = ""
    field_skeleton: str = ""
    exact_hash: str = ""
    structure_signature: str = ""
    behavior_signature: str = ""


@dataclass(frozen=True)
class CandidateInventory:
    records: tuple[InventoryRecord, ...]
    rejection_counts: tuple[tuple[str, int], ...] = ()

    @property
    def expressions(self) -> tuple[str, ...]:
        return tuple(item.expression for item in self.records if item.expression)


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
        return tuple(item.expression for item in self.records if item.grounded and item.expression)


@dataclass(frozen=True)
class LocalSnapshots:
    catalog: MetadataCache
    catalog_dir: Path
    catalog_source: str
    catalog_age_hours: float
    feedback: FeedbackSummary
    inventory: CandidateInventory


def load_catalog_snapshot(
    *,
    root: Path | str = ".",
    catalog_dir: Path | str | None = None,
    allow_stale: bool = True,
    allow_partial_offline: bool = False,
    offline_max_age_hours: float = DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS,
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
    partial_candidates: list[tuple[Path, str]] = []
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
            if allow_partial_offline:
                partial_candidates.append((path, source))
    if allow_partial_offline:
        for path, source in partial_candidates:
            try:
                metadata = MetadataCache.load_for_offline_generation(
                    path,
                    max_age_hours=float(offline_max_age_hours),
                    allow_stale=False,
                )
                return metadata, path, f"{source}-partial-offline", _age_hours(metadata.info)
            except (MetadataCacheMissing, MetadataCacheStale, MetadataCacheError) as fallback_exc:
                errors.append(f"{source}-partial-offline:{type(fallback_exc).__name__}:{str(fallback_exc)[:180]}")
    raise CatalogUnavailable("CATALOG_UNAVAILABLE; " + " | ".join(errors))


def load_local_snapshots(
    *,
    root: Path | str = ".",
    catalog_dir: Path | str | None = None,
    database: Path | str | None = None,
    queue_path: Path | str | None = None,
    allow_partial_offline: bool = False,
    offline_max_age_hours: float = DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS,
) -> LocalSnapshots:
    root_path = Path(root)
    catalog, source_dir, source, age = load_catalog_snapshot(
        root=root_path,
        catalog_dir=catalog_dir,
        allow_partial_offline=allow_partial_offline,
        offline_max_age_hours=offline_max_age_hours,
    )
    db_path = Path(database) if database is not None else root_path / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
    queue = Path(queue_path) if queue_path is not None else root_path / "待提交Alpha列表.csv"
    queue_rows = _read_queue(queue)
    events_path = root_path / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv"
    inventory = load_candidate_inventory(queue_rows, event_rows=_read_queue(events_path))
    feedback = load_feedback_summary(db_path, queue_rows=queue_rows, root=root_path)
    return LocalSnapshots(catalog, source_dir, source, age, feedback, inventory)


def load_feedback_summary(
    database: Path | str,
    *,
    queue_path: Path | str | None = None,
    queue_rows: list[dict[str, str]] | None = None,
    root: Path | str = ".",
) -> FeedbackSummary:
    """Load platform observations only; queue rows are grounding inventory, not feedback."""

    db = Path(database)
    records: list[FeedbackRecord] = []
    inventory_rows = list(queue_rows) if queue_rows is not None else _read_queue(queue_path)
    by_request = {
        str(row.get("request_hash") or ""): row
        for row in inventory_rows
        if str(row.get("request_hash") or "")
    }
    by_candidate = {
        str(row.get("candidate_id") or ""): row
        for row in inventory_rows
        if str(row.get("candidate_id") or "")
    }
    if db.exists():
        try:
            CandidateFeedbackStore(db)
            with sqlite3.connect(db) as con:
                columns = {row[1] for row in con.execute("PRAGMA table_info(candidate_outcomes)")}
                wanted = [
                    "request_hash", "candidate_id", "expression", "outcome", "strategy_family", "dataset", "field_skeleton",
                    "checks_json", "quality_reasons_json", "error_category", "self_correlation", "prod_correlation",
                ]
                if "candidate_outcomes" in _tables(con):
                    query = "SELECT " + ",".join(name if name in columns else "''" for name in wanted) + " FROM candidate_outcomes"
                    for row in con.execute(query):
                        (
                            request_hash, candidate_id, stored_expression, outcome, family, dataset, field_skeleton,
                            checks_json, quality_reasons_json, error_category, self_corr, prod_corr,
                        ) = row
                        request_hash = str(request_hash or "")
                        candidate_id = str(candidate_id or "")
                        source = by_request.get(request_hash) or by_candidate.get(candidate_id) or {}
                        expression = str(stored_expression or source.get("expression") or "").strip()
                        failures = _failure_types(
                            checks_json, quality_reasons_json, error_category, self_corr, prod_corr, outcome,
                        )
                        records.append(
                            FeedbackRecord(
                                _stable_ref("sqlite", request_hash or candidate_id),
                                request_hash,
                                expression,
                                str(outcome or ""),
                                str(family or source.get("operator_family") or ""),
                                _dataset_value(dataset, source.get("datasets")),
                                tuple(failures),
                                "SELF_CORRELATION" in failures or str(self_corr or "").upper() in {"FAIL", "FAILED"},
                                str(field_skeleton or source.get("field_skeleton") or ""),
                                bool(expression),
                            )
                        )
        except sqlite3.Error:
            pass
    for path in _history_csv_paths(Path(root)):
        records.extend(_read_history_csv(path))
    unique = {item.ref_id: item for item in records}
    records = sorted(unique.values(), key=lambda item: item.ref_id)
    positive = tuple(
        item for item in records
        if item.grounded
        and item.outcome.upper() in {"PASS", "READY_TO_SUBMIT"}
        and not item.failure_types
    )
    near_pass = tuple(
        item for item in records
        if item.grounded
        and (item.outcome.upper() == "NEAR_PASS" or "NEAR_PASS" in item.failure_types)
    )
    failures = tuple(
        item for item in records
        if item.failure_types or item.outcome.upper() in {"FAILED", "FAR_FAIL"}
    )
    risk = tuple(item for item in records if item.grounded and item.self_corr_risk)
    counts: dict[str, int] = {}
    for item in records:
        for failure in item.failure_types:
            counts[failure] = counts.get(failure, 0) + 1
    return FeedbackSummary(tuple(records), positive, near_pass, failures, risk, counts)


def load_candidate_inventory(
    rows: Iterable[dict[str, str]],
    *,
    event_rows: Iterable[dict[str, str]] = (),
) -> CandidateInventory:
    records: list[InventoryRecord] = []
    rejection_counts: dict[str, int] = {}
    for row in rows:
        request_hash = str(row.get("request_hash") or "")
        candidate_id = str(row.get("candidate_id") or "")
        expression = str(row.get("expression") or "").strip()
        records.append(
            InventoryRecord(
                ref_id=_stable_ref("inventory", request_hash or candidate_id),
                candidate_id=candidate_id,
                request_hash=request_hash,
                expression=expression,
                queue_status=str(row.get("queue_status") or ""),
                family=str(row.get("operator_family") or ""),
                dataset=str(row.get("datasets") or ""),
                data_fields=_json_string_tuple(row.get("data_fields")),
                research_direction=str(row.get("research_direction") or ""),
                last_error_category=str(row.get("last_error_category") or ""),
                field_skeleton=str(row.get("field_skeleton") or ""),
                exact_hash=str(row.get("exact_hash") or ""),
                structure_signature=str(row.get("structure_signature") or ""),
                behavior_signature=str(row.get("behavior_signature") or ""),
            )
        )
    for item in records:
        if item.last_error_category:
            rejection_counts[item.last_error_category] = rejection_counts.get(item.last_error_category, 0) + 1
    recent_events = list(event_rows)[-100:]
    for row in recent_events:
        if str(row.get("event_type") or "") != "LOCAL_REJECTED":
            continue
        detail = str(row.get("details") or "").strip()
        reason, separator, count_text = detail.rpartition(":")
        if not separator or not reason:
            continue
        try:
            count = int(count_text)
        except ValueError:
            continue
        if count > 0:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + count
    unique = {item.ref_id: item for item in records}
    return CandidateInventory(
        tuple(sorted(unique.values(), key=lambda item: item.ref_id)),
        tuple(sorted(rejection_counts.items())),
    )

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


def _json_string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    raw = str(value or "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    if isinstance(parsed, list):
        return tuple(str(item) for item in parsed if str(item))
    return (str(parsed),) if isinstance(parsed, str) and parsed else ()


def _dataset_value(primary: object, fallback: object) -> str:
    value = str(primary or "").strip()
    if value:
        return value
    raw = str(fallback or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, list) and parsed:
        return str(parsed[0])
    return str(parsed) if isinstance(parsed, str) else raw


def _failure_types(*values: object) -> list[str]:
    aliases = {
        "SHARPE_LOW": "LOW_SHARPE",
        "FITNESS_LOW": "LOW_FITNESS",
        "TURNOVER_HIGH": "HIGH_TURNOVER",
        "TURNOVER_LOW": "LOW_TURNOVER",
        "PRODUCTION_CORRELATION": "PROD_CORRELATION",
    }
    known = (
        "SELF_CORRELATION", "PROD_CORRELATION", "LOW_SHARPE", "LOW_FITNESS",
        "HIGH_TURNOVER", "LOW_TURNOVER", "CONCENTRATED_WEIGHT", "NEAR_PASS",
    )
    failures: set[str] = set()
    for value in values:
        parsed = _json_value(value)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    name = aliases.get(str(item.get("name") or "").upper(), str(item.get("name") or "").upper())
                    status = str(item.get("result") or item.get("status") or "").upper()
                    if name in known and status in {"FAIL", "FAILED", "REJECTED", "ERROR"}:
                        failures.add(name)
                else:
                    _collect_failure_tokens(str(item or ""), failures, aliases, known)
            continue
        _collect_failure_tokens(str(value or ""), failures, aliases, known)
    return [item for item in known if item in failures]


def _json_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _collect_failure_tokens(
    value: str,
    failures: set[str],
    aliases: dict[str, str],
    known: tuple[str, ...],
) -> None:
    text = str(value or "").upper()
    if text in {"PASS", "PASSED", "COMPLETE", "READY_TO_SUBMIT", "WAITING_CHECKS"}:
        return
    for source, canonical in aliases.items():
        if source in text:
            failures.add(canonical)
    for item in known:
        if item in text:
            failures.add(item)


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
