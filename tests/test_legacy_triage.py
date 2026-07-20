"""Tests for alpha_mining.analysis.legacy_triage (Prompt B)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from alpha_mining.analysis.legacy_triage import (
    cluster_by_structure,
    export_to_resim_queue,
    filter_worth_resubmitting,
    load_records,
    run_triage,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a minimal feedback CSV and return its path."""
    if not rows:
        rows = [{}]
    fieldnames = list({k for r in rows for k in r})
    path = tmp_path / "feedback.csv"
    with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _make_sqlite(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "mem.sqlite"
    con = sqlite3.connect(str(path))
    con.execute(
        """CREATE TABLE simulation_runs
           (alpha_id TEXT, expression TEXT, status TEXT, queue_status TEXT,
            sharpe REAL, fitness REAL, turnover REAL, fail_reason TEXT)"""
    )
    for r in rows:
        con.execute(
            "INSERT INTO simulation_runs VALUES (?,?,?,?,?,?,?,?)",
            (
                r.get("alpha_id", ""),
                r.get("expression", ""),
                r.get("status", ""),
                r.get("queue_status", ""),
                r.get("sharpe"),
                r.get("fitness"),
                r.get("turnover"),
                r.get("fail_reason", ""),
            ),
        )
    con.commit()
    con.close()
    return path


def _sample_rows() -> list[dict]:
    """20 synthetic records spanning a range of quality levels."""
    return [
        # High quality — should pass filter
        {
            "alpha_id": "a1",
            "expression": "rank(ts_delta(assets, 252))",
            "family": "fundamental",
            "Sharpe": "2.1",
            "Fitness": "1.3",
            "Turnover": "0.05",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a2",
            "expression": "group_rank(ts_mean(cashflow_op, 63), sector)",
            "family": "fundamental",
            "Sharpe": "1.8",
            "Fitness": "1.1",
            "Turnover": "0.07",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a3",
            "expression": "rank(ts_std(assets, 126))",
            "family": "fundamental",
            "Sharpe": "1.7",
            "Fitness": "1.05",
            "Turnover": "0.04",
            "Failure Reasons": "",
        },
        # Below Sharpe threshold
        {
            "alpha_id": "a4",
            "expression": "rank(ts_delta(close, 5))",
            "family": "pv",
            "Sharpe": "1.3",
            "Fitness": "1.0",
            "Turnover": "0.15",
            "Failure Reasons": "IS_LADDER_SHARPE",
        },
        {
            "alpha_id": "a5",
            "expression": "rank(ts_mean(close, 10))",
            "family": "pv",
            "Sharpe": "1.1",
            "Fitness": "0.9",
            "Turnover": "0.20",
            "Failure Reasons": "PROD_CORRELATION",
        },
        # Turnover out of range
        {
            "alpha_id": "a6",
            "expression": "rank(ts_delta(assets, 63))",
            "family": "fundamental",
            "Sharpe": "1.9",
            "Fitness": "1.2",
            "Turnover": "0.005",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a7",
            "expression": "rank(ts_std(assets, 63))",
            "family": "fundamental",
            "Sharpe": "1.9",
            "Fitness": "1.2",
            "Turnover": "0.80",
            "Failure Reasons": "",
        },
        # Same family, different data fields — should be different clusters
        {
            "alpha_id": "a8",
            "expression": "rank(ts_delta(sales, 252))",
            "family": "fundamental",
            "Sharpe": "1.4",
            "Fitness": "0.8",
            "Turnover": "0.06",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a9",
            "expression": "rank(ts_delta(ebitda, 252))",
            "family": "fundamental",
            "Sharpe": "1.5",
            "Fitness": "0.9",
            "Turnover": "0.06",
            "Failure Reasons": "",
        },
        # Same family, same data fields — should be in SAME cluster
        {
            "alpha_id": "a10",
            "expression": "zscore(ts_delta(assets, 252))",
            "family": "fundamental",
            "Sharpe": "1.55",
            "Fitness": "0.95",
            "Turnover": "0.05",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a11",
            "expression": "-rank(ts_delta(assets, 252))",
            "family": "fundamental",
            "Sharpe": "1.6",
            "Fitness": "1.0",
            "Turnover": "0.05",
            "Failure Reasons": "",
        },
        # Missing Sharpe / Fitness
        {
            "alpha_id": "a12",
            "expression": "rank(close)",
            "family": "pv",
            "Sharpe": "",
            "Fitness": "",
            "Turnover": "0.10",
            "Failure Reasons": "",
        },
        # Fast signal below threshold
        {
            "alpha_id": "a13",
            "expression": "ts_rank(close, 5)",
            "family": "pv",
            "Sharpe": "1.2",
            "Fitness": "0.8",
            "Turnover": "0.25",
            "Failure Reasons": "IS_LADDER_SHARPE",
        },
        {
            "alpha_id": "a14",
            "expression": "ts_rank(vwap, 10)",
            "family": "pv",
            "Sharpe": "1.0",
            "Fitness": "0.7",
            "Turnover": "0.30",
            "Failure Reasons": "IS_LADDER_SHARPE",
        },
        {
            "alpha_id": "a15",
            "expression": "ts_zscore(adv20, 5)",
            "family": "pv",
            "Sharpe": "0.9",
            "Fitness": "0.5",
            "Turnover": "0.35",
            "Failure Reasons": "IS_LADDER_SHARPE",
        },
        # More fundamentals for cluster size testing
        {
            "alpha_id": "a16",
            "expression": "winsorize(ts_delta(assets, 252), 0.1)",
            "family": "fundamental",
            "Sharpe": "1.65",
            "Fitness": "1.0",
            "Turnover": "0.05",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a17",
            "expression": "rank(ts_mean(assets, 252))",
            "family": "fundamental",
            "Sharpe": "1.7",
            "Fitness": "1.1",
            "Turnover": "0.06",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a18",
            "expression": "group_neutralize(ts_delta(assets, 252), market)",
            "family": "fundamental",
            "Sharpe": "1.8",
            "Fitness": "1.2",
            "Turnover": "0.05",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a19",
            "expression": "rank(assets / cap)",
            "family": "fundamental",
            "Sharpe": "1.5",
            "Fitness": "0.9",
            "Turnover": "0.04",
            "Failure Reasons": "",
        },
        {
            "alpha_id": "a20",
            "expression": "zscore(cashflow_op / assets)",
            "family": "fundamental",
            "Sharpe": "1.6",
            "Fitness": "1.0",
            "Turnover": "0.03",
            "Failure Reasons": "",
        },
    ]


# ── filter_worth_resubmitting ─────────────────────────────────────────────────


class TestFilterWorthResubmitting:
    def test_high_quality_kept(self) -> None:
        rows = _sample_rows()
        result = filter_worth_resubmitting(
            rows, min_sharpe=1.65, min_fitness=1.0, min_turnover=0.01, max_turnover=0.70
        )
        ids = {r["alpha_id"] for r in result}
        assert "a1" in ids
        assert "a2" in ids

    def test_low_sharpe_excluded(self) -> None:
        rows = _sample_rows()
        result = filter_worth_resubmitting(
            rows, min_sharpe=1.65, min_fitness=1.0, min_turnover=0.01, max_turnover=0.70
        )
        ids = {r["alpha_id"] for r in result}
        assert "a4" not in ids  # Sharpe 1.3 < 1.65
        assert "a5" not in ids  # Sharpe 1.1

    def test_turnover_out_of_range_excluded(self) -> None:
        rows = _sample_rows()
        result = filter_worth_resubmitting(
            rows,
            min_sharpe=1.65,
            min_fitness=1.0,
            min_turnover=0.01,
            max_turnover=0.70,
        )
        ids = {r["alpha_id"] for r in result}
        assert "a6" not in ids  # Turnover 0.005 < 0.01
        assert "a7" not in ids  # Turnover 0.80 > 0.70

    def test_missing_metrics_excluded(self) -> None:
        rows = _sample_rows()
        result = filter_worth_resubmitting(
            rows, min_sharpe=1.65, min_fitness=1.0, min_turnover=0.01, max_turnover=0.70
        )
        ids = {r["alpha_id"] for r in result}
        assert "a12" not in ids  # empty Sharpe/Fitness

    def test_result_is_small_fraction(self) -> None:
        rows = _sample_rows()
        result = filter_worth_resubmitting(
            rows, min_sharpe=1.65, min_fitness=1.0, min_turnover=0.01, max_turnover=0.70
        )
        assert len(result) < len(rows) // 2


# ── cluster_by_structure ──────────────────────────────────────────────────────


class TestClusterByStructure:
    def test_same_family_and_fields_in_one_cluster(self) -> None:
        # a1 and a11 both use {assets} in family=fundamental
        rows = _sample_rows()
        records = [
            {
                "expression": r["expression"],
                "family": r["family"],
                "sharpe": float(r["Sharpe"]) if r["Sharpe"] else None,
                "fitness": float(r["Fitness"]) if r["Fitness"] else None,
                "turnover": float(r["Turnover"]) if r["Turnover"] else None,
                "failure_reasons": r.get("Failure Reasons", ""),
            }
            for r in rows
        ]
        clusters = cluster_by_structure(records)
        # a1 (Sharpe=2.1, highest) should be in the top-5 representatives of
        # the fundamental/{assets} cluster.
        target_expr = "rank(ts_delta(assets, 252))"
        target_cl = next(
            (
                cl
                for cl in clusters
                if any(r["expression"] == target_expr for r in cl.members)
            ),
            None,
        )
        assert target_cl is not None, f"Expected cluster containing {target_expr!r}"
        # The cluster should also contain a18 (Sharpe=1.8), second highest in this bucket.
        member_exprs = {r["expression"] for r in target_cl.members}
        assert "group_neutralize(ts_delta(assets, 252), market)" in member_exprs

    def test_different_families_in_different_clusters(self) -> None:
        records = [
            {
                "expression": "rank(ts_delta(assets, 252))",
                "family": "fundamental",
                "sharpe": 1.5,
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            },
            {
                "expression": "rank(ts_delta(assets, 252))",
                "family": "pv",
                "sharpe": 1.5,
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            },
        ]
        clusters = cluster_by_structure(records)
        families = {cl.family for cl in clusters}
        assert len(families) == 2

    def test_representatives_capped(self) -> None:
        records = [
            {
                "expression": f"rank(ts_delta(assets, {i}))",
                "family": "f",
                "sharpe": 1.0 + i * 0.01,
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            }
            for i in range(1, 21)
        ]
        clusters = cluster_by_structure(records, representatives_per_cluster=3)
        for cl in clusters:
            assert len(cl.members) <= 3

    def test_empty_expression_skipped(self) -> None:
        records = [
            {
                "expression": "",
                "family": "f",
                "sharpe": 2.0,
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            },
            {
                "expression": "rank(close)",
                "family": "f",
                "sharpe": 1.5,
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            },
        ]
        clusters = cluster_by_structure(records)
        for cl in clusters:
            for r in cl.members:
                assert r["expression"]

    def test_avg_sharpe_computed(self) -> None:
        records = [
            {
                "expression": f"rank(ts_delta(assets, {i}))",
                "family": "f",
                "sharpe": float(i),
                "fitness": 1.0,
                "turnover": 0.05,
                "failure_reasons": "",
            }
            for i in range(1, 4)
        ]
        clusters = cluster_by_structure(records, representatives_per_cluster=10)
        # All have same family and same data field (assets)
        assert len(clusters) == 1
        avg = clusters[0].avg_sharpe
        assert avg is not None


# ── load_records from CSV and SQLite ─────────────────────────────────────────


class TestLoadRecords:
    def test_load_from_csv(self, tmp_path: Path) -> None:
        rows = _sample_rows()
        csv_path = _make_csv(tmp_path, rows)
        records = load_records(csv_path)
        assert len(records) == len(rows)
        assert all(isinstance(r, dict) for r in records)

    def test_load_from_sqlite(self, tmp_path: Path) -> None:
        sqlite_rows = [
            {
                "alpha_id": "b1",
                "expression": "rank(assets)",
                "sharpe": 1.8,
                "fitness": 1.1,
                "turnover": 0.05,
                "fail_reason": "",
            },
            {
                "alpha_id": "b2",
                "expression": "rank(close)",
                "sharpe": 1.2,
                "fitness": 0.8,
                "turnover": 0.15,
                "fail_reason": "CORR",
            },
        ]
        db_path = _make_sqlite(tmp_path, sqlite_rows)
        records = load_records(db_path)
        assert len(records) == 2

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(FileNotFoundError):
            load_records(tmp_path / "nonexistent.csv")


# ── run_triage integration ────────────────────────────────────────────────────


class TestRunTriage:
    def test_writes_both_output_files(self, tmp_path: Path) -> None:
        csv_path = _make_csv(tmp_path, _sample_rows())
        summary = run_triage(
            csv_path,
            output_dir=tmp_path,
            min_sharpe=1.65,
            min_fitness=1.0,
            min_turnover=0.01,
            max_turnover=0.70,
        )
        assert Path(summary["resubmit_path"]).is_file()
        assert Path(summary["clusters_path"]).is_file()

    def test_summary_counts_plausible(self, tmp_path: Path) -> None:
        csv_path = _make_csv(tmp_path, _sample_rows())
        summary = run_triage(
            csv_path,
            output_dir=tmp_path,
            min_sharpe=1.65,
            min_fitness=1.0,
            min_turnover=0.01,
            max_turnover=0.70,
        )
        assert summary["total_records"] == 20
        assert summary["resubmit_candidates"] < summary["total_records"]
        assert summary["clusters"] >= 1

    def test_explicit_dynamic_limit_boundary(self, tmp_path: Path) -> None:
        rows = [
            {
                "alpha_id": "x1",
                "expression": "rank(assets)",
                "family": "f",
                "Sharpe": "1.57",
                "Fitness": "1.1",
                "Turnover": "0.05",
                "Failure Reasons": "",
            },
            {
                "alpha_id": "x2",
                "expression": "rank(ts_delta(assets, 252))",
                "family": "f",
                "Sharpe": "1.58",
                "Fitness": "1.1",
                "Turnover": "0.05",
                "Failure Reasons": "",
            },
        ]
        csv_path = _make_csv(tmp_path, rows)
        summary = run_triage(
            csv_path,
            output_dir=tmp_path,
            min_sharpe=1.57,
            min_fitness=1.0,
            min_turnover=0.01,
            max_turnover=0.70,
        )
        # x1 (Sharpe exactly 1.57) must be excluded; x2 (1.58) must be included.
        assert summary["resubmit_candidates"] == 1

    def test_resim_queue_written_when_path_provided(self, tmp_path: Path) -> None:
        csv_path = _make_csv(tmp_path, _sample_rows())
        queue_path = tmp_path / "alpha_resim_queue.csv"
        summary = run_triage(
            csv_path,
            output_dir=tmp_path,
            min_sharpe=1.65,
            min_fitness=1.0,
            min_turnover=0.01,
            max_turnover=0.70,
            resim_queue_path=queue_path,
        )
        assert summary["queue_written"] >= 0
        assert summary["queue_path"] == str(queue_path)
        if summary["queue_written"] > 0:
            assert queue_path.is_file()


# ── export_to_resim_queue ─────────────────────────────────────────────────────


class TestExportToResimQueue:
    def _qualifying_records(self) -> list[dict]:
        return [
            {
                "alpha_id": "q1",
                "expression": "rank(ts_delta(assets, 252))",
                "sharpe": 2.1,
                "fitness": 1.3,
                "turnover": 0.05,
                "region": "USA",
                "universe": "TOP3000",
                "neutralization": "MARKET",
                "delay": "1",
                "decay": "4",
                "truncation": "0.05",
                "returns": "0.12",
                "drawdown": "0.06",
                "margin": "0.001",
            },
            {
                "alpha_id": "q2",
                "expression": "group_rank(ts_mean(cashflow_op, 63), sector)",
                "sharpe": 1.8,
                "fitness": 1.1,
                "turnover": 0.07,
                "region": "USA",
                "universe": "TOP3000",
                "neutralization": "MARKET",
                "delay": "1",
                "decay": "4",
                "truncation": "0.05",
                "returns": "0.10",
                "drawdown": "0.05",
                "margin": "0.001",
            },
        ]

    def test_writes_pending_rows(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.csv"
        n = export_to_resim_queue(self._qualifying_records(), queue_path)
        assert n == 2
        assert queue_path.is_file()
        with open(str(queue_path), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert all(r["resim_status"] == "PENDING" for r in rows)
        assert all(r["new_alpha_id"] == "" for r in rows)

    def test_settings_json_reconstructed(self, tmp_path: Path) -> None:
        import json as _json

        queue_path = tmp_path / "queue.csv"
        export_to_resim_queue(self._qualifying_records(), queue_path)
        with open(str(queue_path), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        settings = _json.loads(rows[0]["settings_json"])
        assert settings["region"] == "USA"
        assert settings["universe"] == "TOP3000"
        assert settings["neutralization"] == "MARKET"

    def test_duplicate_ids_skipped(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.csv"
        records = self._qualifying_records()
        export_to_resim_queue(records, queue_path)
        # Second call with same records — should not duplicate
        n2 = export_to_resim_queue(records, queue_path)
        assert n2 == 0  # all already in queue
        with open(str(queue_path), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_empty_expression_skipped(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.csv"
        records = [
            {
                "alpha_id": "e1",
                "expression": "",
                "sharpe": 2.0,
                "fitness": 1.2,
                "turnover": 0.05,
            },
            {
                "alpha_id": "e2",
                "expression": "rank(assets)",
                "sharpe": 2.0,
                "fitness": 1.2,
                "turnover": 0.05,
            },
        ]
        n = export_to_resim_queue(records, queue_path)
        assert n == 1  # only e2 (e1 has empty expression)

    def test_platform_simulation_json_used_when_present(self, tmp_path: Path) -> None:
        import json as _json

        settings = {
            "instrumentType": "EQUITY",
            "region": "EUR",
            "universe": "TOP500",
            "neutralization": "CROWDING",
            "delay": 1,
            "decay": 6,
            "truncation": 0.08,
            "pasteurization": "ON",
            "unitHandling": "VERIFY",
            "nanHandling": "ON",
            "maxTrade": "OFF",
            "maxPosition": "OFF",
            "language": "FASTEXPR",
            "visualization": False,
        }
        records = [
            {
                "alpha_id": "p1",
                "expression": "rank(assets / cap)",
                "sharpe": 2.0,
                "fitness": 1.2,
                "turnover": 0.05,
                "platform_simulation_json": _json.dumps(settings),
            }
        ]
        queue_path = tmp_path / "queue.csv"
        export_to_resim_queue(records, queue_path)
        with open(str(queue_path), encoding="utf-8-sig") as f:
            row = list(csv.DictReader(f))[0]
        parsed = _json.loads(row["settings_json"])
        assert parsed["region"] == "EUR"
        assert parsed["universe"] == "TOP500"
        assert parsed["neutralization"] == "CROWDING"
