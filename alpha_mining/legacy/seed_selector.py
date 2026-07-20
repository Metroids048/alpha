"""Deterministic selection of at most one candidate per behavior cluster."""

from __future__ import annotations

from typing import Iterable


def select_cluster_seeds(
    rows: Iterable[dict], *, limit: int | None = None
) -> list[dict]:
    selected: dict[str, dict] = {}
    for row in rows:
        cluster = str(
            row.get("cluster_id")
            or row.get("behavior_signature")
            or row.get("canonical_id")
            or ""
        )
        current = selected.get(cluster)
        score = (
            float(row.get("sharpe") or -999),
            float(row.get("fitness") or -999),
            str(row.get("legacy_id") or ""),
        )
        old = (
            (
                float(current.get("sharpe") or -999),
                float(current.get("fitness") or -999),
                str(current.get("legacy_id") or ""),
            )
            if current
            else None
        )
        if old is None or score > old:
            selected[cluster] = row
    values = sorted(
        selected.values(),
        key=lambda row: (
            -float(row.get("sharpe") or -999),
            str(row.get("legacy_id") or ""),
        ),
    )
    return values if limit is None else values[:limit]
