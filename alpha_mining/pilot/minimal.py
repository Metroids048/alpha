"""Deterministic bounded pilot selection; this module never calls submit."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Iterable


def select_old_alpha_pilot(
    candidates: Iterable[dict[str, Any]], *, limit: int = 100, random_seed: int = 0
) -> list[dict[str, Any]]:
    budget = min(100, max(0, int(limit)))
    rows = [dict(row) for row in candidates if str(row.get("cluster_id") or "") and str(row.get("alpha_id") or "")]
    selected: list[dict[str, Any]] = []
    used: set[str] = set()

    def take(source: Iterable[dict[str, Any]], count: int, stratum: str) -> None:
        for row in source:
            cluster = str(row["cluster_id"])
            if cluster in used:
                continue
            selected.append({**row, "stratum": stratum})
            used.add(cluster)
            if len(selected) >= budget or sum(item["stratum"] == stratum for item in selected) >= count:
                break

    take(sorted(rows, key=lambda row: (-float(row.get("quality") or -999), str(row["cluster_id"]))), 25, "quality")
    take(sorted(rows, key=lambda row: (-float(row.get("structural_distance") or 0), str(row["cluster_id"]))), 20, "structural")
    take((row for row in rows if bool(row.get("near_pass"))), 15, "near_pass")
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("data_category") or "UNKNOWN")].append(row)
    category_round_robin: list[dict[str, Any]] = []
    while by_category and len(category_round_robin) < len(rows):
        exhausted: list[str] = []
        for category in sorted(by_category):
            values = by_category[category]
            if values:
                category_round_robin.append(values.pop(0))
            if not values:
                exhausted.append(category)
        for category in exhausted:
            del by_category[category]
    take(category_round_robin, 20, "data_category")
    remaining = [row for row in rows if str(row["cluster_id"]) not in used]
    random.Random(random_seed).shuffle(remaining)
    take(remaining, 10, "random_control")
    if len(selected) < budget:
        take((row for row in rows if str(row["cluster_id"]) not in used), budget - len(selected), "fill")
    return selected[:budget]


def plan_new_alpha_pilot(
    hypotheses: Iterable[dict[str, Any]], *, limit: int = 40
) -> list[dict[str, Any]]:
    budget = min(40, max(20, int(limit)))
    rows = [dict(row) for row in hypotheses]
    planned: list[dict[str, Any]] = []
    for row in rows:
        if len(planned) >= budget:
            break
        planned.append(
            {
                "hypothesis_id": row.get("hypothesis_id", ""),
                "stage": "baseline",
                "expression": row.get("baseline", ""),
            }
        )
    for row in rows:
        if len(planned) >= budget:
            break
        if str(row.get("baseline_status") or "").upper() != "NEAR_PASS":
            continue
        variant = str(row.get("mechanism_variant") or "").strip()
        if variant:
            planned.append(
                {
                    "hypothesis_id": row.get("hypothesis_id", ""),
                    "stage": "mechanism_variant",
                    "expression": variant,
                }
            )
    return planned[:budget]


def select_description_patch_pilot(
    candidates: Iterable[dict[str, Any]], *, limit: int = 10
) -> list[dict[str, Any]]:
    budget = min(10, max(0, int(limit)))
    return [
        dict(row)
        for row in candidates
        if bool(row.get("checks_complete"))
        and bool(row.get("description_required"))
        and bool(row.get("description_valid"))
    ][:budget]
