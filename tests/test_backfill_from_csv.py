from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from alpha_mining.storage.backfill_from_csv import backfill_csvs, format_summary


def _write_csv(
    path: Path, fieldnames: list[str], rows: list[dict[str, object]]
) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_backfill_maps_case_insensitive_columns_and_deduplicates_normalized_text(
    tmp_path: Path,
) -> None:
    total = tmp_path / "total.csv"
    passed = tmp_path / "passed.csv"
    database = tmp_path / "research.sqlite3"
    _write_csv(
        total,
        [
            "utc_iso",
            "alpha_id",
            "expression",
            "family",
            "status",
            "queue_status",
            "Region",
            "Universe",
            "Neutralization",
            "Decay",
            "Delay",
            "Sharpe",
            "Fitness",
            "Turnover",
            "correlation_max",
            "Failure Reasons",
        ],
        [
            {
                "utc_iso": "2026-07-17T00:00:00Z",
                "alpha_id": "alpha-1",
                "expression": "rank(ts_delta(close, 5))",
                "family": "template_arch_A",
                "status": "ok",
                "queue_status": "done",
                "Region": "USA",
                "Universe": "TOP3000",
                "Neutralization": "INDUSTRY",
                "Decay": "4",
                "Delay": "1",
                "Sharpe": "1.5",
                "Fitness": "1.1",
                "Turnover": "0.2",
                "correlation_max": "0.43",
                "Failure Reasons": "",
            },
            {
                "utc_iso": "2026-07-17T01:00:00Z",
                "alpha_id": "alpha-2",
                "expression": "rank(volume)",
                "family": "",
                "status": "failed",
                "queue_status": "",
                "Region": "EUR",
                "Sharpe": "0.5",
                "Fitness": "0.4",
                "Turnover": "0.3",
                "Failure Reasons": "LOW_SHARPE",
            },
            {"expression": "", "family": "ignored"},
        ],
    )
    _write_csv(
        passed,
        [
            "expression",
            "alpha_id",
            "sharpe",
            "fitness",
            "turnover",
            "returns",
            "drawdown",
        ],
        [
            {
                "expression": "rank(ts_delta(close, 10))",
                "alpha_id": "duplicate-by-normalization",
                "sharpe": "9.9",
                "fitness": "9.9",
                "turnover": "0.1",
                "returns": "0.2",
                "drawdown": "0.05",
            }
        ],
    )

    summary = backfill_csvs(database, [total, passed])

    assert summary.rows_scanned == 4
    assert summary.rows_imported == 2
    assert summary.blank_expressions == 1
    assert summary.duplicates_skipped == 1
    assert summary.by_strategy["template_arch_A"].count == 1
    assert summary.by_strategy["template_arch_A"].avg_sharpe == 1.5
    # Metric values alone no longer impersonate a platform PASS.
    assert summary.by_strategy["template_arch_A"].pass_rate == 0.0
    assert summary.by_strategy["legacy_unknown"].count == 1
    assert summary.by_strategy["legacy_unknown"].avg_fitness == 0.4
    assert summary.by_strategy["legacy_unknown"].pass_rate == 0.0

    with sqlite3.connect(database) as connection:
        expressions = connection.execute(
            """
            SELECT expression_id, expression_text, normalized_text, structure_sig,
                   generation_strategy, generation_layer
            FROM expressions ORDER BY expression_text
            """
        ).fetchall()
        assert len(expressions) == 2
        assert all(
            row[0].startswith("expr_") and len(row[0]) == 69 for row in expressions
        )
        assert all(row[2] and row[3] for row in expressions)
        assert {row[4] for row in expressions} == {"template_arch_A", "legacy_unknown"}
        assert {row[5] for row in expressions} == {"L4"}

        simulations = connection.execute(
            """
            SELECT alpha_id, status, queue_status, sharpe, fitness, turnover,
                   region, universe, neutralization, decay, delay, correlation_max, fail_reason,
                   expression_id
            FROM simulation_runs ORDER BY alpha_id
            """
        ).fetchall()
        assert len(simulations) == 2
        assert simulations[0][0:13] == (
            "alpha-1",
            "ok",
            "done",
            1.5,
            1.1,
            0.2,
            "USA",
            "TOP3000",
            "INDUSTRY",
            4,
            1,
            0.43,
            "",
        )
        assert all(row[13] for row in simulations)

        topic_stats = connection.execute(
            """
            SELECT t.topic_name_en, s.total_generated, s.total_simulated,
                   s.total_passed_gate, s.pass_rate, s.avg_sharpe, s.avg_fitness
            FROM topic_stats s
            JOIN research_topics t ON t.topic_id = s.topic_id
            ORDER BY t.topic_name_en
            """
        ).fetchall()
        assert topic_stats == [
            ("legacy_unknown", 1, 1, 0, 0.0, 0.5, 0.4),
            ("template_arch_A", 1, 1, 0, 0.0, 1.5, 1.1),
        ]


def test_backfill_is_idempotent_across_repeated_runs(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    database = tmp_path / "research.sqlite3"
    _write_csv(
        source,
        ["expression", "family", "Sharpe", "Fitness"],
        [
            {
                "expression": "rank(close)",
                "family": "legacy_family",
                "Sharpe": "1.2",
                "Fitness": "1.0",
            }
        ],
    )

    first = backfill_csvs(database, [source])
    second = backfill_csvs(database, [source])

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert second.duplicates_skipped == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expressions").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0]
            == 1
        )


def test_summary_text_contains_totals_and_strategy_averages(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    database = tmp_path / "research.sqlite3"
    _write_csv(
        source,
        ["expression", "family", "Sharpe", "Fitness"],
        [
            {
                "expression": "rank(close)",
                "family": "family_a",
                "Sharpe": "1",
                "Fitness": "2",
            },
            {
                "expression": "rank(volume)",
                "family": "family_a",
                "Sharpe": "3",
                "Fitness": "4",
            },
        ],
    )

    text = format_summary(backfill_csvs(database, [source]))

    assert "scanned=2" in text
    assert "imported=2" in text
    assert (
        "family_a: count=2 pass_rate=0.000000 avg_sharpe=2.000000 avg_fitness=3.000000"
    ) in text


def test_backfill_module_imports_normalization_helpers_instead_of_reimplementing_them() -> (
    None
):
    source = (
        Path(__file__).resolve().parents[1]
        / "alpha_mining"
        / "storage"
        / "backfill_from_csv.py"
    ).read_text(encoding="utf-8")
    assert "_normalized_expression" in source
    assert "_structure_signature" in source
    assert "_platform_pass_proxy" not in source
    assert "def _normalized_expression" not in source
    assert "def _structure_signature" not in source
