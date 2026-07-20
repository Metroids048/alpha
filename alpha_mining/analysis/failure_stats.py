"""Prompt C: failure-rate statistics and region/universe/neutralization diversity analysis."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

_TS_WINDOW_RE = re.compile(r"\bts_[a-z_]+\([^,]+,\s*(\d+)", re.I)
_PRICE_FIELDS = frozenset(
    {
        "close",
        "open",
        "high",
        "low",
        "vwap",
        "returns",
        "volume",
        "adv20",
        "adv5",
        "adv60",
        "adv120",
    }
)
_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_]*\b")
_DEFAULT_FAST_WINDOW = 21


def _is_fast_signal(expression: str, *, max_window: int = _DEFAULT_FAST_WINDOW) -> bool:
    """Mirror of ladder_check.is_fast_signal to avoid circular import."""
    expr = str(expression or "")
    windows = [int(m) for m in _TS_WINDOW_RE.findall(expr)]
    if not windows or max(windows) > max_window:
        return False
    return bool(frozenset(_TOKEN_RE.findall(expr.lower())) & _PRICE_FIELDS)


def _to_float(val: Any) -> float | None:
    try:
        return (
            float(val)
            if val is not None and str(val).strip() not in ("", "nan", "None")
            else None
        )
    except (TypeError, ValueError):
        return None


def _load_feedback_csv(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(str(path), encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ── statistics ────────────────────────────────────────────────────────────────


def _failure_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator > 0 else 0.0


def compute_failure_stats(
    feedback_csv: str | Path,
    *,
    min_simulated_rows: int = 5,
) -> dict[str, Any]:
    """Compute failure-rate statistics from alpha_submission_feedback.csv.

    Analyses three dimensions:
    1. Fast vs slow signal failure rates (PROD_CORRELATION, IS_LADDER_SHARPE)
    2. Region/Universe distribution and per-bucket failure rates
    3. Neutralization distribution and per-setting failure rates

    Returns a nested dict with all statistics.  Rows with no expression are
    skipped.  Groups with fewer than min_simulated_rows samples are marked as
    insufficient_data.
    """
    rows = _load_feedback_csv(feedback_csv)

    # ── per-row classification ────────────────────────────────────────────────
    fast_stats: dict[str, dict[str, int]] = {
        "fast": defaultdict(int),
        "slow": defaultdict(int),
    }
    region_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    universe_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    neut_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        expr = str(row.get("expression") or "").strip()
        if not expr:
            continue

        failure_text = str(
            row.get("Failure Reasons") or row.get("fail_reason") or ""
        ).upper()
        prod_corr_fail = "PROD_CORRELATION" in failure_text
        ladder_fail = "IS_LADDER_SHARPE" in failure_text or "LADDER" in failure_text
        any_fail = (
            prod_corr_fail
            or ladder_fail
            or (str(row.get("check_passed") or "").strip().lower() == "false")
        )

        speed = "fast" if _is_fast_signal(expr) else "slow"
        fast_stats[speed]["total"] += 1
        if prod_corr_fail:
            fast_stats[speed]["prod_correlation_fail"] += 1
        if ladder_fail:
            fast_stats[speed]["is_ladder_fail"] += 1
        if any_fail:
            fast_stats[speed]["any_fail"] += 1

        region = (
            str(row.get("Region") or row.get("region") or "unknown").strip()
            or "unknown"
        )
        universe = (
            str(row.get("Universe") or row.get("universe") or "unknown").strip()
            or "unknown"
        )
        neut = (
            str(
                row.get("Neutralization") or row.get("neutralization") or "unknown"
            ).strip()
            or "unknown"
        )
        region_stats[region]["total"] += 1
        universe_stats[universe]["total"] += 1
        neut_stats[neut]["total"] += 1

        if prod_corr_fail:
            region_stats[region]["prod_correlation_fail"] += 1
            universe_stats[universe]["prod_correlation_fail"] += 1
        if ladder_fail:
            region_stats[region]["is_ladder_fail"] += 1
            universe_stats[universe]["is_ladder_fail"] += 1
        if any_fail:
            region_stats[region]["any_fail"] += 1
            universe_stats[universe]["any_fail"] += 1
            neut_stats[neut]["any_fail"] += 1

    # ── compute rates ─────────────────────────────────────────────────────────
    def _rates(counts: dict[str, int]) -> dict[str, Any]:
        n = counts.get("total", 0)
        if n < min_simulated_rows:
            return {"total": n, "status": "insufficient_data"}
        return {
            "total": n,
            "prod_correlation_fail_rate": _failure_rate(
                counts.get("prod_correlation_fail", 0), n
            ),
            "is_ladder_fail_rate": _failure_rate(counts.get("is_ladder_fail", 0), n),
            "any_fail_rate": _failure_rate(counts.get("any_fail", 0), n),
        }

    fast_results = {speed: _rates(dict(c)) for speed, c in fast_stats.items()}
    region_results = {k: _rates(dict(v)) for k, v in region_stats.items()}
    universe_results = {k: _rates(dict(v)) for k, v in universe_stats.items()}
    neut_results = {k: _rates(dict(v)) for k, v in neut_stats.items()}

    # ── derive diversity recommendations ─────────────────────────────────────
    fast = fast_results.get("fast", {})
    slow = fast_results.get("slow", {})
    fast_corr_rate = fast.get("prod_correlation_fail_rate", 0.0)
    slow_corr_rate = slow.get("prod_correlation_fail_rate", 0.0)
    fast_ladder_rate = fast.get("is_ladder_fail_rate", 0.0)
    slow_ladder_rate = slow.get("is_ladder_fail_rate", 0.0)

    # Recommended fast_signal_penalty: proportional to how much worse fast signals
    # are than slow ones on PROD_CORRELATION + IS_LADDER combined.
    corr_delta = max(0.0, fast_corr_rate - slow_corr_rate)
    ladder_delta = max(0.0, fast_ladder_rate - slow_ladder_rate)
    combined_delta = corr_delta + ladder_delta
    # Scale: delta of 0.1 → penalty ~0.1; capped at 0.5 to avoid excessive suppression.
    recommended_fast_penalty = round(min(0.5, combined_delta), 3)

    # Identify crowded region/universe buckets (top 3 by sample count).
    ru_counts = sorted(
        [
            (k, v.get("total", 0))
            for k, v in region_results.items()
            if isinstance(v, dict) and "total" in v
        ],
        key=lambda x: x[1],
        reverse=True,
    )
    top3_regions = [r for r, _ in ru_counts[:3]]
    crowded_universe_flag = universe_results.get("TOP3000", {}).get("total", 0) > 0

    hypotheses_supported = {
        "fast_signal_is_main_corr_failure_driver": fast_corr_rate
        > slow_corr_rate * 1.2,
        "fast_signal_is_main_ladder_failure_driver": fast_ladder_rate
        > slow_ladder_rate * 1.2,
        "usa_top3000_dominates_sample": (
            region_results.get("USA", {}).get("total", 0)
            > sum(v.get("total", 0) for k, v in region_results.items() if k != "USA")
            * 2
        ),
    }

    return {
        "total_rows_analyzed": sum(s.get("total", 0) for s in fast_results.values()),
        "fast_vs_slow": fast_results,
        "by_region": region_results,
        "by_universe": universe_results,
        "by_neutralization": neut_results,
        "recommendations": {
            "recommended_fast_signal_penalty": recommended_fast_penalty,
            "corr_delta_fast_minus_slow": round(corr_delta, 4),
            "ladder_delta_fast_minus_slow": round(ladder_delta, 4),
            "top_3_regions_by_volume": top3_regions,
            "crowded_usa_top3000": crowded_universe_flag,
        },
        "hypotheses_supported": hypotheses_supported,
    }


def print_stats_report(stats: dict[str, Any]) -> None:
    """Print a human-readable summary of compute_failure_stats output."""
    print(f"\n=== Failure Stats Report (n={stats.get('total_rows_analyzed', '?')}) ===")

    fvs = stats.get("fast_vs_slow", {})
    for speed in ("fast", "slow"):
        s = fvs.get(speed, {})
        if s.get("status") == "insufficient_data":
            print(f"  {speed}: insufficient data (n={s.get('total', 0)})")
        else:
            print(
                f"  {speed:4s}: n={s.get('total', 0):5d}  "
                f"prod_corr_fail={s.get('prod_correlation_fail_rate', 0):.1%}  "
                f"ladder_fail={s.get('is_ladder_fail_rate', 0):.1%}"
            )

    rec = stats.get("recommendations", {})
    print(
        f"\n  → recommended fast_signal_penalty = {rec.get('recommended_fast_signal_penalty', 0)}"
    )
    print(
        f"  → corr delta (fast-slow)   = {rec.get('corr_delta_fast_minus_slow', 0):.4f}"
    )
    print(
        f"  → ladder delta (fast-slow) = {rec.get('ladder_delta_fast_minus_slow', 0):.4f}"
    )

    by_region = stats.get("by_region", {})
    if by_region:
        print("\n  Region distribution:")
        for region, s in sorted(
            by_region.items(), key=lambda x: x[1].get("total", 0), reverse=True
        )[:6]:
            if (
                isinstance(s, dict)
                and "total" in s
                and s.get("status") != "insufficient_data"
            ):
                print(
                    f"    {region:12s}: n={s['total']:5d}  corr_fail={s.get('prod_correlation_fail_rate', 0):.1%}"
                )

    hyp = stats.get("hypotheses_supported", {})
    print("\n  Hypotheses:")
    for k, v in hyp.items():
        print(f"    {k}: {'SUPPORTED' if v else 'NOT SUPPORTED'}")
