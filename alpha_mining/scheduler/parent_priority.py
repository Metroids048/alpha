"""Lexicographic parent priority; historical Sharpe cannot outrank correlation evidence."""

from __future__ import annotations

from typing import Any, Iterable


def _score(row: dict[str, Any]) -> tuple[float, float, float, float, float, str]:
    self_correlation = 1.0 if str(row.get("self_corr_status") or "").upper() == "PASS" else 0.0
    prod_correlation = 1.0 if str(row.get("prod_corr_status") or "").upper() == "PASS" else 0.0
    return (
        self_correlation,
        prod_correlation,
        float(row.get("quality") or 0.0),
        float(row.get("robustness") or 0.0),
        float(row.get("mechanism_novelty") or 0.0),
        str(row.get("id") or ""),
    )


def rank_parents(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=_score, reverse=True)
