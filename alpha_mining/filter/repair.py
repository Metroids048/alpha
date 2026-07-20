"""L6 Repair Engine — structured failure classification and repair strategy selection."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from alpha_mining.storage.sqlite_store import SqliteRunLog

# ─── failure classification ──────────────────────────────────────────────────

# Maps platform feedback tokens → canonical failure category
_FAILURE_PATTERNS: list[tuple[str, str]] = [
    (r"SELF.?CORR", "SELF_CORRELATION"),
    (r"PROD(?:UCTION)?[ _.-]?CORR", "PROD_CORRELATION"),
    (r"IS[ _.-]?LADDER|LADDER[ _.-]?SHARPE", "IS_LADDER_SHARPE"),
    (r"HIGH.?TURN", "HIGH_TURNOVER"),
    (r"CONC[EI]?N?T", "CONCENTRATED_WEIGHT"),
    (r"SPARSE|BREADTH|LOW.?BREADTH", "SPARSE_SIGNAL"),
    (r"INCOMPAT|UNIT|TYPE.?ERR|SYNTAX|INVALID.?EXPR", "INCOMPATIBLE_UNIT"),
    (r"LOW.?FITNESS", "LOW_FITNESS"),
    (r"LOW.?SHARPE", "LOW_SHARPE"),
]

# Maps failure category → recommended repair strategy
_REPAIR_STRATEGIES: dict[str, str] = {
    "LOW_SHARPE": "add_cross_sectional_norm",
    "LOW_FITNESS": "adjust_decay_or_window",
    "HIGH_TURNOVER": "lengthen_window_or_add_decay",
    "CONCENTRATED_WEIGHT": "add_group_neutralize_or_winsorize",
    "SELF_CORRELATION": "trigger_l5_mutation_change_hypothesis",
    "PROD_CORRELATION": "change_data_or_operator_family",
    "IS_LADDER_SHARPE": "stabilize_regime_with_longer_horizon",
    "SPARSE_SIGNAL": "broaden_universe_or_field",
    "INCOMPATIBLE_UNIT": "blacklist_field_combo_and_regen",
}

# ─── inline repair transforms ────────────────────────────────────────────────


def _repair_low_sharpe(expr: str) -> str | None:
    """Wrap top-level expression with group_neutralize(rank(...), market)."""
    if not re.search(r"\bgroup_neutralize\s*\(", expr):
        return f"group_neutralize(rank({expr}), market)"
    return None


def _repair_high_turnover(expr: str) -> str | None:
    """Replace shortest numeric window with a longer one (minimum 21)."""
    match = re.search(r"\b(\d+)\b", expr)
    if match:
        w = int(match.group(1))
        new_w = max(21, w * 2)
        if new_w != w:
            return expr[: match.start()] + str(new_w) + expr[match.end() :]
    return None


def _repair_concentrated_weight(expr: str) -> str | None:
    """Add winsorize wrapper if not already present."""
    if not re.search(r"\bwinsorize\s*\(", expr):
        return f"winsorize({expr})"
    return None


_INLINE_REPAIRS: dict[str, Callable[[str], str | None]] = {
    "LOW_SHARPE": _repair_low_sharpe,
    "HIGH_TURNOVER": _repair_high_turnover,
    "CONCENTRATED_WEIGHT": _repair_concentrated_weight,
}

# ─── data types ──────────────────────────────────────────────────────────────


@dataclass
class RepairResult:
    failure_category: str
    repair_strategy: str
    repaired_expression: str | None  # None means L4 re-generation is required
    needs_regen: bool = False


# ─── engine ──────────────────────────────────────────────────────────────────


class RepairEngine:
    """Classify platform failures and attempt inline repairs or signal L4 re-generation."""

    def classify(self, fail_reason: str) -> str:
        """Return the canonical failure category for a platform feedback string."""
        text = fail_reason.upper()
        for pattern, category in _FAILURE_PATTERNS:
            if re.search(pattern, text):
                return category
        return "LOW_SHARPE"  # conservative default

    def classify_all(self, fail_reason: str) -> list[str]:
        """Return all matching categories (a single run may hit multiple gates)."""
        text = fail_reason.upper()
        seen: set[str] = set()
        categories: list[str] = []
        for pattern, category in _FAILURE_PATTERNS:
            if category not in seen and re.search(pattern, text):
                categories.append(category)
                seen.add(category)
        return categories or ["LOW_SHARPE"]

    def repair(self, expression: str, failure_category: str) -> RepairResult:
        strategy = _REPAIR_STRATEGIES.get(
            failure_category, "trigger_l5_mutation_change_hypothesis"
        )
        needs_regen = failure_category in (
            "SELF_CORRELATION",
            "PROD_CORRELATION",
            "IS_LADDER_SHARPE",
            "INCOMPATIBLE_UNIT",
            "SPARSE_SIGNAL",
        )
        repaired: str | None = None
        if not needs_regen:
            fn = _INLINE_REPAIRS.get(failure_category)
            if fn is not None:
                repaired = fn(expression)
        return RepairResult(
            failure_category=failure_category,
            repair_strategy=strategy,
            repaired_expression=repaired,
            needs_regen=needs_regen or (repaired is None),
        )


# ─── persistence ─────────────────────────────────────────────────────────────


def persist_repair(
    db: SqliteRunLog,
    *,
    expression_id: str,
    failure_category: str,
    failure_detail: str,
    repair_strategy: str,
    resulting_expression_id: str | None,
    success: bool | None = None,
    repair_id: str | None = None,
) -> str:
    """Write one row to the repairs table; returns the repair_id used."""
    rid = repair_id or str(uuid.uuid4())
    if not db.path:
        return rid
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db.path)) as con:
        con.execute(
            "INSERT OR IGNORE INTO repairs "
            "(repair_id, expression_id, failure_category, failure_detail, "
            " repair_strategy, resulting_expression_id, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid,
                expression_id,
                failure_category,
                failure_detail,
                repair_strategy,
                resulting_expression_id,
                None if success is None else int(success),
                now,
            ),
        )
    return rid
