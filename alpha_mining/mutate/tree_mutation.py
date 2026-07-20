"""L5 Mutation Engine — systematic expression mutation along five axes."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from alpha_mining.storage.sqlite_store import SqliteRunLog

# ─── substitution tables ────────────────────────────────────────────────────

_OPERATOR_SUBS: dict[str, list[str]] = {
    "ts_rank": ["ts_zscore", "rank"],
    "ts_zscore": ["ts_rank", "rank"],
    "ts_std_dev": ["ts_variance"],
    "ts_delta": ["ts_pct_change"],
    "ts_pct_change": ["ts_delta"],
    "rank": ["zscore", "ts_rank"],
    "zscore": ["rank", "winsorize"],
    "winsorize": ["rank", "zscore"],
    "group_rank": ["group_zscore", "group_neutralize"],
    "group_zscore": ["group_rank", "group_neutralize"],
    "group_neutralize": ["group_rank", "group_zscore"],
}

_WINDOW_CANDIDATES = [5, 10, 21, 42, 63, 126, 252]

_NEUTRAL_SUBS: dict[str, list[str]] = {
    "subindustry": ["industry", "market"],
    "industry": ["subindustry", "market"],
    "market": ["subindustry", "industry"],
}

_NORM_WRAPPERS = ["rank", "zscore", "winsorize"]

# ─── axis functions ──────────────────────────────────────────────────────────


def _apply_operator(expr: str, limit: int) -> list[tuple[str, str]]:
    """Replace the first matched operator token."""
    for op, candidates in _OPERATOR_SUBS.items():
        pattern = rf"\b{re.escape(op)}\s*\("
        if re.search(pattern, expr):
            results = []
            for replacement in candidates[:limit]:
                mutated = re.sub(pattern, f"{replacement}(", expr, count=1)
                if mutated != expr:
                    results.append((mutated, f"{op} -> {replacement}"))
            return results
    return []


def _apply_window(expr: str, limit: int) -> list[tuple[str, str]]:
    """Replace the first numeric window arg (≥4) with adjacent candidates."""
    match = re.search(r"\b([4-9]\d{0,2}|[1-9]\d+)\b", expr)
    if not match:
        return []
    orig = int(match.group(1))
    idx = min(
        range(len(_WINDOW_CANDIDATES)), key=lambda i: abs(_WINDOW_CANDIDATES[i] - orig)
    )
    neighbours = [
        _WINDOW_CANDIDATES[i]
        for i in (idx - 1, idx + 1)
        if 0 <= i < len(_WINDOW_CANDIDATES) and _WINDOW_CANDIDATES[i] != orig
    ][:limit]
    return [
        (expr[: match.start()] + str(w) + expr[match.end() :], f"window {orig} -> {w}")
        for w in neighbours
    ]


def _apply_normalization(expr: str, limit: int) -> list[tuple[str, str]]:
    """Wrap the expression in a normalization layer not already present."""
    existing = {w for w in _NORM_WRAPPERS if re.search(rf"\b{w}\s*\(", expr)}
    results: list[tuple[str, str]] = []
    for wrapper in _NORM_WRAPPERS:
        if wrapper not in existing:
            results.append((f"{wrapper}({expr})", f"wrap with {wrapper}"))
            if len(results) >= limit:
                break
    return results


def _apply_neutralization(expr: str, limit: int) -> list[tuple[str, str]]:
    """Substitute the first neutralization scope token."""
    for token, candidates in _NEUTRAL_SUBS.items():
        if re.search(rf"\b{re.escape(token)}\b", expr):
            results = []
            for replacement in candidates[:limit]:
                mutated = re.sub(rf"\b{re.escape(token)}\b", replacement, expr, count=1)
                if mutated != expr:
                    results.append((mutated, f"{token} -> {replacement}"))
            return results
    return []


def _apply_composite(
    expr: str, peer_exprs: Sequence[str], limit: int
) -> list[tuple[str, str]]:
    """Linearly combine with up to `limit` peer expressions."""
    return [
        (f"({expr}) + ({peer})", "composite with peer")
        for peer in list(peer_exprs)[:limit]
        if peer.strip() != expr.strip()
    ]


_AXIS_FN = {
    "operator": _apply_operator,
    "window": _apply_window,
    "normalization": _apply_normalization,
    "neutralization": _apply_neutralization,
}

# ─── result type ─────────────────────────────────────────────────────────────


@dataclass
class MutationResult:
    axis: str
    detail: str
    mutated_expression: str
    parent_expression_id: str = ""
    mutation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ─── engine ──────────────────────────────────────────────────────────────────


class MutationEngine:
    """Produce systematic variants of an expression along up to five axes."""

    def __init__(self, *, max_per_axis: int = 2) -> None:
        self.max_per_axis = max_per_axis

    def mutate(
        self,
        expression: str,
        axis: str,
        *,
        peer_exprs: Sequence[str] = (),
        parent_expression_id: str = "",
    ) -> list[MutationResult]:
        if axis == "composite":
            pairs = _apply_composite(expression, peer_exprs, self.max_per_axis)
        else:
            fn = _AXIS_FN.get(axis)
            if fn is None:
                raise ValueError(f"Unknown mutation axis: {axis!r}")
            pairs = fn(expression, self.max_per_axis)
        return [
            MutationResult(
                axis=axis,
                detail=detail,
                mutated_expression=mutated,
                parent_expression_id=parent_expression_id,
            )
            for mutated, detail in pairs
        ]

    def mutate_all_axes(
        self,
        expression: str,
        *,
        peer_exprs: Sequence[str] = (),
        parent_expression_id: str = "",
    ) -> list[MutationResult]:
        results: list[MutationResult] = []
        for axis in (
            "operator",
            "window",
            "normalization",
            "neutralization",
            "composite",
        ):
            results.extend(
                self.mutate(
                    expression,
                    axis,
                    peer_exprs=peer_exprs,
                    parent_expression_id=parent_expression_id,
                )
            )
        return results


# ─── persistence ─────────────────────────────────────────────────────────────


def persist_mutation(
    db: SqliteRunLog,
    *,
    parent_expression_id: str,
    child_expression_id: str,
    axis: str,
    detail: str,
    mutation_id: str | None = None,
) -> str:
    """Write one row to the mutations table; returns the mutation_id used."""
    mid = mutation_id or str(uuid.uuid4())
    if not db.path:
        return mid
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db.path)) as con:
        con.execute(
            "INSERT OR IGNORE INTO mutations "
            "(mutation_id, parent_expression_id, child_expression_id, "
            " mutation_axis, mutation_detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, parent_expression_id, child_expression_id, axis, detail, now),
        )
    return mid
