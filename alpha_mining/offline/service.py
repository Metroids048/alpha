"""Application service for deterministic offline CSV generation."""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha_mining.description.offline_draft import build_offline_description
from alpha_mining.generation.canonical import canonical_signature
from alpha_mining.generation.families import GeneratedExpression, generate_candidate_pool
from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.storage.csv_queue import CandidateCsvQueue

from .metadata import MetadataCache, MetadataCacheStale
from .scoring import score_candidate_rows


@dataclass(frozen=True)
class OfflineGenerationSummary:
    requested: int
    added: int
    rejected: int
    existing: int
    queue_path: Path
    family_counts: dict[str, int]


class OfflineCandidatePoolExhausted(RuntimeError):
    """Cached metadata cannot produce the requested number of unique structures."""


def run_offline_generation(
    *,
    cache_dir: Path | str,
    queue_path: Path | str,
    events_path: Path | str,
    count: int = 100,
    cache_max_age_hours: float = 168,
    allow_stale_cache: bool = False,
    failure_history_path: Path | str | None = None,
    group_rank_enabled: bool = False,
) -> OfflineGenerationSummary:
    """Generate locally validated candidates without importing any online adapter."""

    requested = max(0, int(count))
    try:
        metadata = MetadataCache.load(cache_dir, max_age_hours=cache_max_age_hours)
    except MetadataCacheStale:
        if not allow_stale_cache:
            raise
        warnings.warn(
            "平台元数据缓存已过期；已按显式参数继续离线生成。",
            UserWarning,
            stacklevel=2,
        )
        metadata = MetadataCache.load(
            cache_dir, max_age_hours=cache_max_age_hours, allow_stale=True
        )

    queue = CandidateCsvQueue(queue_path, events_path)
    validator = LocalExpressionValidator(metadata)
    failure_path = (
        Path(failure_history_path)
        if failure_history_path is not None
        else Path(queue_path).with_name("历史失败.json")
    )
    blocked_families, blocked_skeletons = _active_failure_cooldowns(failure_path)
    candidates = _round_robin(generate_candidate_pool(metadata))

    added = 0
    rejected = 0
    family_counts: dict[str, int] = {}
    with queue.writer():
        existing_rows = queue.read()
        exact_seen: set[str] = set()
        skeleton_seen: set[str] = set()
        for row in existing_rows:
            try:
                signature = json.loads(row.get("canonical_signature") or "{}")
            except json.JSONDecodeError:
                continue
            exact_seen.add(str(signature.get("exact_hash") or ""))
            skeleton_seen.add(str(signature.get("skeleton") or ""))

        active_families = {candidate.family for candidate in candidates} - blocked_families
        quota = max(1, math.ceil(requested / max(1, len(active_families))))
        for candidate in candidates:
            if len(existing_rows) + added >= requested:
                break
            if candidate.family in blocked_families:
                rejected += 1
                continue
            if family_counts.get(candidate.family, 0) >= quota:
                continue
            issues = validator.validate(candidate.expression)
            if issues:
                rejected += 1
                continue
            signature = canonical_signature(
                candidate.expression,
                metadata,
                generator_family=candidate.family,
                parent_template=candidate.parent_template,
                neutralization="SUBINDUSTRY",
                direction=candidate.direction,
            )
            exact = str(signature["exact_hash"])
            skeleton = str(signature["skeleton"])
            if not skeleton or exact in exact_seen or skeleton in skeleton_seen or skeleton in blocked_skeletons:
                rejected += 1
                continue
            if not group_rank_enabled and "group_rank" in signature["operator_multiset"]:
                rejected += 1
                continue
            row = _candidate_row(candidate, signature, metadata)
            queue.upsert(row)
            queue.transition(row["candidate_id"], "QUEUED", "local validation passed")
            exact_seen.add(exact)
            skeleton_seen.add(skeleton)
            family_counts[candidate.family] = family_counts.get(candidate.family, 0) + 1
            added += 1
        queue.replace_all(score_candidate_rows(queue.read()))

    existing_count = len(existing_rows)
    final_count = existing_count + added
    if final_count < requested:
        raise OfflineCandidatePoolExhausted(
            f"元数据约束下候选池不足：目标 {requested}，实际 {final_count}；"
            "请先同步更完整的平台元数据或降低 --count。"
        )
    return OfflineGenerationSummary(
        requested=requested,
        added=added,
        rejected=rejected,
        existing=existing_count,
        queue_path=Path(queue_path),
        family_counts=dict(sorted(family_counts.items())),
    )


def _round_robin(candidates: list[GeneratedExpression]) -> list[GeneratedExpression]:
    grouped: dict[str, list[GeneratedExpression]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.family, []).append(candidate)
    result: list[GeneratedExpression] = []
    for index in range(max((len(rows) for rows in grouped.values()), default=0)):
        for rows in grouped.values():
            if index < len(rows):
                result.append(rows[index])
    return result


def _candidate_row(
    candidate: GeneratedExpression,
    signature: dict[str, Any],
    metadata: MetadataCache,
) -> dict[str, str]:
    exact_hash = str(signature["exact_hash"])
    candidate_id = "candidate_" + hashlib.sha256(
        f"{exact_hash}\0{metadata.info['region']}\0{metadata.info['universe']}\0{metadata.info['delay']}".encode("utf-8")
    ).hexdigest()[:24]
    fields = list(signature["fields"])
    datasets = list(signature["datasets"])
    description = build_offline_description(
        expression=candidate.expression,
        field_ids=fields,
        metadata=metadata,
        hypothesis=candidate.hypothesis,
        direction=candidate.direction,
        windows=list(signature["time_windows"]),
        neutralization="SUBINDUSTRY",
    )
    return {
        "candidate_id": candidate_id,
        "expression": candidate.expression,
        "alpha_type": "REGULAR",
        "region": str(metadata.info["region"]),
        "universe": str(metadata.info["universe"]),
        "delay": str(metadata.info["delay"]),
        "decay": "0",
        "neutralization": "SUBINDUSTRY",
        "truncation": "0.08",
        "language": "FASTEXPR",
        "data_fields": json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
        "datasets": json.dumps(datasets, ensure_ascii=False, separators=(",", ":")),
        "operator_family": candidate.family,
        "canonical_signature": json.dumps(
            signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        "generator_source": "offline_metadata_family_v1",
        "parent_template": candidate.parent_template,
        "economic_hypothesis": candidate.hypothesis,
        "description_draft": description,
        "local_score": "",
        "priority_score": "",
        "queue_status": "GENERATED",
        "retry_count": "0",
    }


def _active_failure_cooldowns(path: Path) -> tuple[set[str], set[str]]:
    if not path.is_file():
        return set(), set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), set()
    rows = payload if isinstance(payload, list) else payload.get("records", []) if isinstance(payload, dict) else []
    now = datetime.now(timezone.utc)
    families: set[str] = set()
    skeletons: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cooldown = str(row.get("cooldown_until") or "")
        try:
            until = datetime.fromisoformat(cooldown.replace("Z", "+00:00"))
        except ValueError:
            continue
        if until.tzinfo is None or until.astimezone(timezone.utc) <= now:
            continue
        family = str(row.get("family") or "")
        skeleton = str(row.get("canonical_skeleton") or row.get("skeleton") or "")
        if family:
            families.add(family)
        if skeleton:
            skeletons.add(skeleton)
    return families, skeletons
