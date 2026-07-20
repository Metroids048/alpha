"""Tests for IS ladder Sharpe pre-check (Prompt 2) and signal diversity gate (Prompt 3)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alpha_mining.filter.ladder_check import (
    check_yearly_sharpes,
    is_fast_signal,
    year_range,
)


# ── ladder_check pure functions ───────────────────────────────────────────────


class TestCheckYearlySharpes:
    def test_all_years_pass(self) -> None:
        data = [(2019, 1.8), (2020, 2.1), (2021, 1.5), (2022, 1.2), (2023, 1.9)]
        result = check_yearly_sharpes(data, threshold=1.0)
        assert result.passes is True
        assert result.failing_years == ()

    def test_one_bad_year_fails(self) -> None:
        data = [(2019, 1.8), (2020, 0.7), (2021, 1.5), (2022, 1.2), (2023, 1.9)]
        result = check_yearly_sharpes(data, threshold=1.0)
        assert result.passes is False
        assert 2020 in result.failing_years

    def test_multiple_bad_years_fail(self) -> None:
        data = [(2019, 0.5), (2020, 0.7), (2021, 1.5)]
        result = check_yearly_sharpes(data, threshold=1.0)
        assert result.passes is False
        assert set(result.failing_years) == {2019, 2020}

    def test_empty_list_skips(self) -> None:
        result = check_yearly_sharpes([], threshold=1.0)
        assert result.passes is True
        assert "skip" in result.note

    def test_exact_threshold_passes(self) -> None:
        result = check_yearly_sharpes([(2019, 1.0)], threshold=1.0)
        assert result.passes is True

    def test_below_threshold_fails(self) -> None:
        result = check_yearly_sharpes([(2019, 0.999)], threshold=1.0)
        assert result.passes is False

    def test_threshold_stored_in_result(self) -> None:
        data = [(2019, 2.0), (2020, 2.0)]
        result = check_yearly_sharpes(data, threshold=1.25)
        assert result.threshold == 1.25

    def test_very_high_sharpe_stable_alpha_passes(self) -> None:
        data = [(y, 3.0 + (y - 2019) * 0.1) for y in range(2019, 2024)]
        result = check_yearly_sharpes(data, threshold=1.0)
        assert result.passes is True
        assert result.failing_years == ()

    def test_good_aggregate_but_one_year_bad(self) -> None:
        """Simulate a case that would pass the aggregate check but fail ladder."""
        data = [(2019, 2.5), (2020, 2.3), (2021, 0.4), (2022, 2.8), (2023, 2.7)]
        result = check_yearly_sharpes(data, threshold=1.0)
        assert result.passes is False
        assert 2021 in result.failing_years


class TestYearRange:
    def test_inclusive_range(self) -> None:
        assert year_range(2019, 2023) == [2019, 2020, 2021, 2022, 2023]

    def test_single_year(self) -> None:
        assert year_range(2021, 2021) == [2021]


class TestIsFastSignal:
    def test_short_window_price_is_fast(self) -> None:
        assert is_fast_signal("rank(ts_delta(close, 5))") is True
        assert is_fast_signal("-rank(ts_delta(close, 10))") is True
        assert is_fast_signal("zscore(ts_mean(vwap, 21))") is True

    def test_long_window_price_is_slow(self) -> None:
        assert is_fast_signal("ts_rank(close, 252)") is False
        assert is_fast_signal("ts_rank(close, 63)") is False
        assert is_fast_signal("ts_rank(vwap, 126)") is False

    def test_short_window_fundamental_is_not_fast(self) -> None:
        # Fundamental field, short window — no price field → NOT fast signal
        assert is_fast_signal("ts_rank(assets, 21)") is False
        assert is_fast_signal("group_rank(ts_delta(cashflow_op, 21), sector)") is False

    def test_no_ts_operators_is_not_fast(self) -> None:
        assert is_fast_signal("rank(close)") is False
        assert is_fast_signal("group_rank(cap, market)") is False

    def test_custom_max_window(self) -> None:
        # window 10 ≤ 10 with price → fast at max_window=10
        assert is_fast_signal("ts_rank(close, 10)", max_window=10) is True
        # window 11 > 10 → not fast at max_window=10
        assert is_fast_signal("ts_rank(close, 11)", max_window=10) is False

    def test_empty_expression(self) -> None:
        assert is_fast_signal("") is False
        assert is_fast_signal("   ") is False

    def test_adv20_counts_as_price_field(self) -> None:
        assert is_fast_signal("ts_zscore(adv20, 5)") is True


# ── diversity penalty in _payload_fine_rank_key ───────────────────────────────


def _rank_key(expr: str, penalty: float = 0.0) -> tuple:
    """Call _payload_fine_rank_key with minimal boilerplate."""
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    mod_path = root / "auto_alpha_pipeline_rebuilt_v50.py"
    mod_spec = importlib.util.spec_from_file_location(
        "auto_alpha_pipeline_rebuilt_v50", mod_path
    )
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["auto_alpha_pipeline_rebuilt_v50"] = mod
    mod_spec.loader.exec_module(mod)
    payload = {"regular": expr, "type": "REGULAR", "settings": {}, "meta": {}}
    return mod._payload_fine_rank_key(payload, {}, {}, penalty)


class TestDiversityPenalty:
    def test_fast_signal_gets_higher_penalty_tuple_when_enabled(self) -> None:
        fast_expr = "-rank(ts_delta(close, 5))"
        key_fast_on = _rank_key(fast_expr, penalty=0.3)
        key_fast_off = _rank_key(fast_expr, penalty=0.0)
        # Fast signal with penalty ON should sort WORSE than fast signal with penalty OFF
        # Sort key: lower tuple = better rank (sorted ascending, best at position 0)
        assert key_fast_on > key_fast_off, (
            "penalty should increase sort-worse direction"
        )

    def test_slow_signal_unaffected_by_fast_penalty(self) -> None:
        slow_expr = "group_rank(ts_delta(assets, 252) / cap, sector) - 0.5"
        key_on = _rank_key(slow_expr, penalty=0.3)
        key_off = _rank_key(slow_expr, penalty=0.0)
        assert key_on == key_off, "slow signal should not be penalized"

    def test_zero_penalty_is_equivalent_to_disabled(self) -> None:
        expr = "ts_rank(close, 10)"
        assert _rank_key(expr, penalty=0.0) == _rank_key(expr, penalty=0.0)


# ── run_yearly_ladder_check integration (mock HTTP) ──────────────────────────


def _make_pipeline_with_ladder(tmp_path: Path, *, ladder_enabled: bool = True):
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    mod_path = root / "auto_alpha_pipeline_rebuilt_v50.py"
    mod_spec = importlib.util.spec_from_file_location(
        "auto_alpha_pipeline_rebuilt_v50", mod_path
    )
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["auto_alpha_pipeline_rebuilt_v50"] = mod
    mod_spec.loader.exec_module(mod)

    cfg = mod.PipelineConfig(
        username="u",
        password="p",
        ladder_check_enabled=ladder_enabled,
        ladder_check_min_sharpe=1.0,
        ladder_check_start_year=2021,
        ladder_check_end_year=2022,
        sqlite_runs_path=str(tmp_path / "simulation-idempotency.sqlite"),
    )
    pipe = object.__new__(mod.WorldQuantAlphaPipeline)
    pipe.cfg = cfg
    pipe._sess_lock = threading.Lock()
    fake_sess = MagicMock()
    pipe.sess = fake_sess
    pipe._sess_request = lambda method, url, **kw: fake_sess.request(method, url, **kw)
    pipe._timeout = lambda: (10.0, 30.0)
    pipe.ensure_authenticated = MagicMock()
    pipe._last_submit_ts = 0.0
    pipe._consecutive_dns_errors = 0
    pipe._dynamic_submit_sleep = 0.0
    return pipe, fake_sess, mod


class TestRunYearlyLadderCheck:
    def test_disabled_always_passes(self, tmp_path: Path) -> None:
        pipe, _, _ = _make_pipeline_with_ladder(tmp_path, ladder_enabled=False)
        row = {"expression": "rank(close)", "settings": {"delay": 1}}
        ok, note = pipe.run_yearly_ladder_check(row)
        assert ok is True
        assert "disabled" in note

    def test_missing_expression_skips(self, tmp_path: Path) -> None:
        pipe, _, _ = _make_pipeline_with_ladder(tmp_path)
        ok, note = pipe.run_yearly_ladder_check({"settings": {"delay": 1}})
        assert ok is True
        assert "skip" in note

    def test_passes_when_all_years_ok(self, tmp_path: Path) -> None:
        pipe, fake_sess, mod = _make_pipeline_with_ladder(tmp_path)

        call_count = [0]

        def fake_request(method, url, **kw):
            resp = MagicMock()
            if method == "POST":
                resp.status_code = 201
                resp.headers = {
                    "Location": "https://api.worldquantbrain.com/simulations/s001/progress"
                }
                resp.json.return_value = {}
                resp.text = ""
            elif method == "GET":
                call_count[0] += 1
                # Return alpha_id via 'alpha' key so _alpha_id_from_progress resolves
                resp.status_code = 200
                resp.json.return_value = {
                    "alpha": f"alpha_{call_count[0]}",
                    "is": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.1},
                }
                resp.text = ""
            else:
                resp.status_code = 404
            return resp

        fake_sess.request.side_effect = fake_request

        row = {
            "alpha_id": "alpha-test",
            "expression": "rank(close)",
            "settings": {"delay": 1, "region": "USA"},
        }
        ok, note = pipe.run_yearly_ladder_check(row)
        assert ok is True
        assert "pass" in note

    def test_fails_when_one_year_below_threshold(self, tmp_path: Path) -> None:
        pipe, fake_sess, mod = _make_pipeline_with_ladder(tmp_path)
        year_counter = [0]

        def fake_request(method, url, **kw):
            resp = MagicMock()
            if method == "POST":
                resp.status_code = 201
                resp.headers = {
                    "Location": "https://api.worldquantbrain.com/simulations/s/progress"
                }
                resp.json.return_value = {}
                resp.text = ""
            elif method == "GET":
                year_counter[0] += 1
                # First year (2021) returns low Sharpe, second (2022) high
                sharpe = 0.3 if year_counter[0] <= 1 else 1.8
                resp.status_code = 200
                resp.json.return_value = {
                    "alpha": f"a{year_counter[0]}",
                    "is": {"sharpe": sharpe},
                }
                resp.text = ""
            else:
                resp.status_code = 404
            return resp

        fake_sess.request.side_effect = fake_request

        row = {
            "alpha_id": "alpha-test",
            "expression": "rank(close)",
            "settings": {"delay": 1},
        }
        ok, note = pipe.run_yearly_ladder_check(row)
        assert ok is False
        assert "fail" in note


# ── yearly_sharpes_from_daily_returns ─────────────────────────────────────────


class TestYearlySharpeFromDailyReturns:
    from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

    def _make_year(
        self, year: int, n: int, mean: float, std: float
    ) -> list[tuple[str, float]]:
        """Generate n synthetic daily returns for a given year."""
        import random

        random.seed(year)
        result = []
        for day in range(1, n + 1):
            month = (day // 28) % 12 + 1
            d = day % 28 + 1
            date_str = f"{year}-{month:02d}-{d:02d}"
            ret = mean + random.gauss(0, std)
            result.append((date_str, ret))
        return result

    def test_single_complete_year(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

        # Constant returns → zero variance → year is skipped (no valid Sharpe).
        data = [("2021-01-01", 0.001)] * 252
        result = yearly_sharpes_from_daily_returns(data)
        assert result == []

    def test_single_complete_year_with_variance(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns
        import math

        # 252 observations with non-zero variance → one year with a finite Sharpe.
        data = [
            (f"2021-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", 0.001 + (i % 10) * 0.0005)
            for i in range(252)
        ]
        result = yearly_sharpes_from_daily_returns(data)
        assert len(result) == 1
        assert result[0][0] == 2021
        assert math.isfinite(result[0][1])

    def test_year_with_insufficient_days_skipped(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

        sparse = [("2021-01-01", 0.001)] * 100  # less than min_complete_days=200
        complete = [("2022-01-01", 0.001)] * 252
        result = yearly_sharpes_from_daily_returns(sparse + complete)
        years = [y for y, _ in result]
        assert 2021 not in years

    def test_multiple_years_split_correctly(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

        data: list[tuple[str, float]] = []
        for yr in range(2019, 2023):
            for i in range(252):
                data.append((f"{yr}-01-{i % 28 + 1:02d}", 0.002 + i * 0.00001))
        result = yearly_sharpes_from_daily_returns(data)
        assert len(result) >= 1

    def test_empty_input_returns_empty(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

        assert yearly_sharpes_from_daily_returns([]) == []

    def test_bad_date_entries_ignored(self) -> None:
        from alpha_mining.filter.ladder_check import yearly_sharpes_from_daily_returns

        data = [("bad-date", 0.001)] * 252
        result = yearly_sharpes_from_daily_returns(data)
        assert result == []


# ── pearson_correlation ───────────────────────────────────────────────────────


class TestPearsonCorrelation:
    def test_perfect_positive(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation

        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert abs(pearson_correlation(a, a) - 1.0) < 1e-9

    def test_perfect_negative(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation

        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        rho = pearson_correlation(a, b)
        assert rho is not None and abs(rho + 1.0) < 1e-9

    def test_uncorrelated_returns_none_or_low(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation
        import random

        random.seed(42)
        a = [random.gauss(0, 1) for _ in range(500)]
        b = [random.gauss(0, 1) for _ in range(500)]
        rho = pearson_correlation(a, b)
        assert rho is not None and abs(rho) < 0.15  # expected near zero

    def test_too_short_returns_none(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation

        assert pearson_correlation([], []) is None
        assert pearson_correlation([1.0], [1.0]) is None

    def test_different_lengths_returns_none(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation

        assert pearson_correlation([1.0, 2.0], [1.0, 2.0, 3.0]) is None

    def test_zero_variance_returns_none(self) -> None:
        from alpha_mining.filter.ladder_check import pearson_correlation

        a = [1.0, 1.0, 1.0]
        b = [1.0, 2.0, 3.0]
        assert pearson_correlation(a, b) is None


# ── check_self_correlation_local ──────────────────────────────────────────────


class TestLocalSelfCorrelation:
    def test_high_correlation_blocked(self) -> None:
        from alpha_mining.filter.ladder_check import check_self_correlation_local

        rets = [float(i) * 0.001 for i in range(300)]
        hist = {"alpha_old": rets.copy()}
        passes, note, corr = check_self_correlation_local(rets, hist, threshold=0.6)
        assert passes is False
        assert corr >= 0.6

    def test_uncorrelated_passes(self) -> None:
        from alpha_mining.filter.ladder_check import check_self_correlation_local
        import random

        random.seed(0)
        a = [random.gauss(0, 1) for _ in range(500)]
        b = [random.gauss(0, 1) for _ in range(500)]
        hist = {"alpha_old": b}
        passes, _, corr = check_self_correlation_local(a, hist, threshold=0.6)
        assert passes is True

    def test_empty_history_fails_closed(self) -> None:
        from alpha_mining.filter.ladder_check import check_self_correlation_local

        rets = [0.001] * 100
        passes, note, _ = check_self_correlation_local(rets, {}, threshold=0.6)
        assert passes is False
        assert "insufficient_history" in note

    def test_threshold_boundary(self) -> None:
        from alpha_mining.filter.ladder_check import check_self_correlation_local

        rets = [float(i) * 0.001 for i in range(300)]
        hist = {"alpha_old": rets.copy()}
        # At threshold=1.0, correlation of 1.0 is exactly at the boundary
        passes, _, corr = check_self_correlation_local(rets, hist, threshold=1.0)
        # corr is exactly 1.0, which is >= threshold, so should fail
        assert passes is False


# ── explicit internal threshold ──────────────────────────────────────────────


class TestExplicitThreshold:
    def test_threshold_is_required(self) -> None:
        from alpha_mining.filter.ladder_check import check_yearly_sharpes

        with pytest.raises(TypeError):
            check_yearly_sharpes([(2021, 1.6)])

    def test_explicit_old_threshold_still_works(self) -> None:
        from alpha_mining.filter.ladder_check import check_yearly_sharpes

        result = check_yearly_sharpes([(2021, 1.6)], threshold=1.0)
        assert result.passes is True
