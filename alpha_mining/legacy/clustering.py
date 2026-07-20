"""Deterministic behavior clustering and medoid selection."""

from __future__ import annotations

import hashlib
import random
from typing import Any, Sequence

from .features import feature_distance


def deterministic_medoid(
    members: Sequence[dict[str, Any]], *, exact_limit: int = 500
) -> dict[str, Any]:
    if not members:
        raise ValueError("members must not be empty")
    ordered = sorted(members, key=lambda item: str(item.get("legacy_id") or ""))
    candidates = ordered
    comparison = ordered
    if len(ordered) > exact_limit:
        seed = int(
            hashlib.sha256(
                "|".join(str(x.get("legacy_id")) for x in ordered).encode()
            ).hexdigest()[:16],
            16,
        )
        rng = random.Random(seed)
        sample_size = min(exact_limit, max(64, int(len(ordered) ** 0.5) * 4))
        candidates = sorted(
            rng.sample(ordered, sample_size),
            key=lambda item: str(item.get("legacy_id")),
        )
        comparison = candidates
    scored = [
        (
            sum(feature_distance(candidate, other) for other in comparison),
            str(candidate.get("legacy_id") or ""),
            candidate,
        )
        for candidate in candidates
    ]
    return min(scored, key=lambda item: (item[0], item[1]))[2]


def cluster_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record.get("behavior_signature") or ""), []).append(
            record
        )
    out = []
    for signature, members in sorted(buckets.items()):
        cluster_id = "cluster_" + hashlib.sha256(signature.encode()).hexdigest()[:20]
        out.append(
            {
                "cluster_id": cluster_id,
                "behavior_signature": signature,
                "members": members,
                "medoid": deterministic_medoid(members),
            }
        )
    return out
