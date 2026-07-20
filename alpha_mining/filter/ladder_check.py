"""IS ladder Sharpe pre-check utilities (local / platform-side both use these)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


# ── fast-signal detection ─────────────────────────────────────────────────────

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

_TS_WINDOW_RE = re.compile(r"\bts_[a-z_]+\([^,]+,\s*(\d+)", re.I)
_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_]*\b")

_DEFAULT_FAST_WINDOW = 21


def is_fast_signal(expression: str, *, max_window: int = _DEFAULT_FAST_WINDOW) -> bool:
    """Return True when expression uses short ts_* windows with price/volume fields.

    Heuristic: all ts_* operator windows <= max_window AND at least one
    recognised price/volume field is present.
    """
    expr = str(expression or "")
    windows = [int(m) for m in _TS_WINDOW_RE.findall(expr)]
    if not windows or max(windows) > max_window:
        return False
    tokens = frozenset(_TOKEN_RE.findall(expr.lower()))
    return bool(tokens & _PRICE_FIELDS)


# ── per-year Sharpe check ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LadderResult:
    passes: bool
    yearly_sharpes: tuple[tuple[int, float], ...]  # (year, sharpe)
    failing_years: tuple[int, ...]
    threshold: float
    note: str


def check_yearly_sharpes(
    yearly_sharpes: list[tuple[int, float]],
    *,
    threshold: float,
) -> LadderResult:
    """Evaluate whether every simulated year meets the Sharpe threshold.

    Args:
        yearly_sharpes: list of (year, sharpe) pairs.
        threshold: explicit internal research margin supplied by the caller.

    Returns:
        LadderResult with passes=True only when every year >= threshold.
    """
    if not yearly_sharpes:
        return LadderResult(True, (), (), threshold, "no_data:skip")
    failing = [y for y, s in yearly_sharpes if s < threshold]
    passes = len(failing) == 0
    note = "pass" if passes else f"fail:years={','.join(str(y) for y in failing)}"
    return LadderResult(
        passes=passes,
        yearly_sharpes=tuple(yearly_sharpes),
        failing_years=tuple(failing),
        threshold=threshold,
        note=note,
    )


def year_range(start_year: int, end_year: int) -> list[int]:
    """Return list of years [start_year .. end_year] inclusive."""
    return list(range(int(start_year), int(end_year) + 1))


# ── local computation from daily return series ────────────────────────────────

_TRADING_DAYS_PER_YEAR = 252.0


def yearly_sharpes_from_daily_returns(
    daily_returns: list[tuple[str, float]],
    *,
    min_complete_days: int = 200,
) -> list[tuple[int, float]]:
    """Compute per-year annualised Sharpe from (date_str, return) pairs.

    Splits by calendar year (YYYY prefix of date_str).  Years with fewer than
    min_complete_days trading observations are skipped to avoid noise from
    partial years at the edges of the back-test window.

    Returns a sorted list of (year, sharpe) pairs — empty when no year has
    sufficient data.  Never raises.
    """
    if not daily_returns:
        return []

    by_year: dict[int, list[float]] = {}
    for date_str, ret in daily_returns:
        try:
            yr = int(str(date_str)[:4])
        except (ValueError, TypeError):
            continue
        by_year.setdefault(yr, []).append(float(ret))

    result: list[tuple[int, float]] = []
    for yr, rets in sorted(by_year.items()):
        if len(rets) < min_complete_days:
            continue
        mean = sum(rets) / len(rets)
        variance = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = math.sqrt(variance)
        if std == 0.0:
            continue
        sharpe = (mean / std) * math.sqrt(_TRADING_DAYS_PER_YEAR)
        if math.isfinite(sharpe):
            result.append((yr, sharpe))

    return result


# ── return-series correlation ─────────────────────────────────────────────────


def pearson_correlation(
    returns_a: list[float],
    returns_b: list[float],
) -> float | None:
    """Pearson correlation of two return series aligned by position.

    Returns None when the series are too short (< 2 observations), have zero
    variance, or are of different lengths.  Never raises.
    """
    n = len(returns_a)
    if n < 2 or n != len(returns_b):
        return None
    mean_a = sum(returns_a) / n
    mean_b = sum(returns_b) / n
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(returns_a, returns_b)) / n
    var_a = sum((a - mean_a) ** 2 for a in returns_a) / n
    var_b = sum((b - mean_b) ** 2 for b in returns_b) / n
    if var_a == 0.0 or var_b == 0.0:
        return None
    rho = cov / math.sqrt(var_a * var_b)
    return max(-1.0, min(1.0, rho)) if math.isfinite(rho) else None


def check_self_correlation_local(
    candidate_returns: list[float],
    historical_returns_map: dict[str, list[float]],
    *,
    threshold: float,
    min_overlap: int = 60,
) -> tuple[bool, str, float]:
    """Check whether a candidate is too correlated with any historical alpha.

    Args:
        candidate_returns: daily return series for the new candidate.
        historical_returns_map: {alpha_id: daily_returns} for previously
            submitted alphas.  Keys are only used for diagnostic output.
        threshold: explicit internal safety margin; never a platform limit.
        min_overlap: minimum aligned observations required for any conclusion.

    Returns:
        (passes, note, max_correlation)
        passes=True means no historical alpha exceeded the threshold.
    """
    if not candidate_returns or not historical_returns_map:
        return False, "insufficient_history:no_reference", 0.0

    max_corr = 0.0
    worst_id = ""
    sufficient = False
    for alpha_id, hist_rets in historical_returns_map.items():
        # Align by taking the shorter length (trailing truncation).
        n = min(len(candidate_returns), len(hist_rets))
        if n < max(2, int(min_overlap)):
            continue
        rho = pearson_correlation(candidate_returns[:n], hist_rets[:n])
        if rho is not None:
            sufficient = True
            if not worst_id or abs(rho) > max_corr:
                max_corr = abs(rho)
                worst_id = alpha_id

    if not sufficient:
        return False, "insufficient_history:min_overlap", 0.0
    if max_corr >= threshold:
        return False, f"local_self_corr:{max_corr:.3f}:vs:{worst_id}", max_corr
    return True, f"local_self_corr:ok:{max_corr:.3f}", max_corr
