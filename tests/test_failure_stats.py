"""Tests for alpha_mining.analysis.failure_stats (Prompt C)."""

from __future__ import annotations

import csv
from pathlib import Path

from alpha_mining.analysis.failure_stats import (
    compute_failure_stats,
    print_stats_report,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_feedback_csv(tmp_path: Path, rows: list[dict]) -> Path:
    if not rows:
        rows = [{}]
    fieldnames = list({k for r in rows for k in r})
    path = tmp_path / "feedback.csv"
    with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _fast_rows(n: int, fail_reason: str = "PROD_CORRELATION") -> list[dict]:
    return [
        {
            "expression": f"rank(ts_delta(close, {5 + i % 5}))",
            "family": "pv",
            "Sharpe": "1.2",
            "Fitness": "0.8",
            "Turnover": "0.20",
            "check_passed": "False",
            "Failure Reasons": fail_reason,
            "Region": "USA",
            "Universe": "TOP3000",
            "Neutralization": "MARKET",
        }
        for i in range(n)
    ]


def _slow_rows(n: int, fail_reason: str = "") -> list[dict]:
    return [
        {
            "expression": f"rank(ts_delta(assets, {63 + i * 10}))",
            "family": "fundamental",
            "Sharpe": "1.8",
            "Fitness": "1.2",
            "Turnover": "0.05",
            "check_passed": "True",
            "Failure Reasons": fail_reason,
            "Region": "EUR",
            "Universe": "TOP1000",
            "Neutralization": "CROWDING",
        }
        for i in range(n)
    ]


# ── compute_failure_stats ─────────────────────────────────────────────────────


class TestComputeFailureStats:
    def test_fast_corr_fail_rate_higher_than_slow(self, tmp_path: Path) -> None:
        rows = _fast_rows(30, "PROD_CORRELATION") + _slow_rows(30, "")
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        fvs = stats["fast_vs_slow"]
        fast_rate = fvs["fast"].get("prod_correlation_fail_rate", 0)
        slow_rate = fvs["slow"].get("prod_correlation_fail_rate", 0)
        assert fast_rate > slow_rate, (
            "fast signals should have higher PROD_CORRELATION failure rate"
        )

    def test_ladder_fail_stats_captured(self, tmp_path: Path) -> None:
        rows = _fast_rows(20, "IS_LADDER_SHARPE") + _slow_rows(20, "")
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        fast_ladder = stats["fast_vs_slow"]["fast"].get("is_ladder_fail_rate", 0)
        assert fast_ladder > 0

    def test_region_distribution_counted(self, tmp_path: Path) -> None:
        rows = _fast_rows(15) + _slow_rows(10)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert "USA" in stats["by_region"]
        assert "EUR" in stats["by_region"]

    def test_universe_distribution_counted(self, tmp_path: Path) -> None:
        rows = _fast_rows(15) + _slow_rows(10)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert "TOP3000" in stats["by_universe"]
        assert "TOP1000" in stats["by_universe"]

    def test_neutralization_counted(self, tmp_path: Path) -> None:
        rows = _fast_rows(15) + _slow_rows(10)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert "MARKET" in stats["by_neutralization"]

    def test_recommended_penalty_positive_when_fast_dominates(
        self, tmp_path: Path
    ) -> None:
        rows = _fast_rows(40, "PROD_CORRELATION") + _slow_rows(40, "")
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        penalty = stats["recommendations"]["recommended_fast_signal_penalty"]
        assert penalty > 0.0

    def test_recommended_penalty_zero_when_no_difference(self, tmp_path: Path) -> None:
        # Both fast and slow have same fail rates → no penalty needed
        rows = _fast_rows(20, "") + _slow_rows(20, "")
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        penalty = stats["recommendations"]["recommended_fast_signal_penalty"]
        assert penalty == 0.0

    def test_rows_without_expression_skipped(self, tmp_path: Path) -> None:
        rows = [
            {"expression": "", "Failure Reasons": "PROD_CORRELATION", "Region": "USA"},
            {"Failure Reasons": "IS_LADDER_SHARPE", "Region": "USA"},
        ] + _fast_rows(10)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert stats["total_rows_analyzed"] == 10

    def test_crowded_usa_top3000_flag(self, tmp_path: Path) -> None:
        rows = _fast_rows(30) + _slow_rows(5)  # fast rows are USA/TOP3000
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert stats["recommendations"]["crowded_usa_top3000"] is True

    def test_hypothesis_usa_dominates(self, tmp_path: Path) -> None:
        # All USA rows: should flag usa_top3000_dominates_sample
        rows = _fast_rows(60)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert stats["hypotheses_supported"]["usa_top3000_dominates_sample"] is True

    def test_returns_all_expected_keys(self, tmp_path: Path) -> None:
        rows = _fast_rows(10) + _slow_rows(10)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        assert "total_rows_analyzed" in stats
        assert "fast_vs_slow" in stats
        assert "by_region" in stats
        assert "by_universe" in stats
        assert "by_neutralization" in stats
        assert "recommendations" in stats
        assert "hypotheses_supported" in stats


# ── print_stats_report (smoke test) ──────────────────────────────────────────


class TestPrintStatsReport:
    def test_no_exception(self, tmp_path: Path) -> None:
        rows = _fast_rows(20) + _slow_rows(20)
        path = _write_feedback_csv(tmp_path, rows)
        stats = compute_failure_stats(path)
        print_stats_report(stats)  # should not raise
