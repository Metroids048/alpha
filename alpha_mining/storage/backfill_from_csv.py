"""Backfill legacy alpha CSV files into the Chapter 4 Research Memory schema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.domain.expression_normalization import (
    _normalized_expression,
    _structure_signature,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = ROOT / "research_memory.sqlite"
DEFAULT_SOURCES = (ROOT / "总alpha.csv", ROOT / "通过门槛的alpha.csv")


@dataclass
class StrategyStats:
    count: int = 0
    passed_count: int = 0
    sharpe_sum: float = 0.0
    sharpe_count: int = 0
    fitness_sum: float = 0.0
    fitness_count: int = 0

    @property
    def avg_sharpe(self) -> float | None:
        return self.sharpe_sum / self.sharpe_count if self.sharpe_count else None

    @property
    def avg_fitness(self) -> float | None:
        return self.fitness_sum / self.fitness_count if self.fitness_count else None

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.count if self.count else 0.0

    def add(
        self, sharpe: float | None, fitness: float | None, *, passed: bool = False
    ) -> None:
        self.count += 1
        if passed:
            self.passed_count += 1
        if sharpe is not None:
            self.sharpe_sum += sharpe
            self.sharpe_count += 1
        if fitness is not None:
            self.fitness_sum += fitness
            self.fitness_count += 1


@dataclass
class BackfillSummary:
    rows_scanned: int = 0
    rows_imported: int = 0
    duplicates_skipped: int = 0
    blank_expressions: int = 0
    by_strategy: dict[str, StrategyStats] = field(default_factory=dict)


def _normalized_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        str(key or "").strip().casefold(): str(value or "").strip()
        for key, value in row.items()
    }


def _text(row: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name.casefold(), "").strip()
        if value:
            return value
    return default


def _float(row: Mapping[str, str], *names: str) -> float | None:
    value = _text(row, *names)
    if not value:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int(row: Mapping[str, str], *names: str) -> int | None:
    value = _float(row, *names)
    return int(value) if value is not None else None


def _expression_id(normalized_text: str) -> str:
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    return f"expr_{digest}"


def _legacy_topic_id(strategy: str) -> str:
    digest = hashlib.sha256(strategy.strip().casefold().encode("utf-8")).hexdigest()[
        :16
    ]
    return f"legacy_strategy_{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iter_csv(path: Path) -> Iterable[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"backfill source does not exist: {path}")
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as stream:
        for row in csv.DictReader(stream):
            if isinstance(row, dict):
                yield _normalized_row(row)


def _refresh_legacy_topic_stats(
    connection: sqlite3.Connection, updated_at: str
) -> None:
    aggregates: dict[str, tuple[set[str], StrategyStats]] = {}
    rows = connection.execute(
        """
        SELECT e.expression_id, e.generation_strategy, s.id, s.sharpe, s.fitness, s.status, s.queue_status
        FROM expressions e
        LEFT JOIN simulation_runs s ON s.expression_id = e.expression_id
        """
    )
    for (
        expression_id,
        strategy,
        simulation_id,
        sharpe,
        fitness,
        status,
        queue_status,
    ) in rows:
        strategy_name = str(strategy or "legacy_unknown")
        expression_ids, stats = aggregates.setdefault(
            strategy_name, (set(), StrategyStats())
        )
        expression_ids.add(str(expression_id))
        if simulation_id is not None:
            observed = {str(status or "").lower(), str(queue_status or "").lower()}
            stats.add(
                sharpe, fitness, passed=bool(observed & {"pass", "passed", "submitted"})
            )

    for strategy, (expression_ids, stats) in aggregates.items():
        topic_id = _legacy_topic_id(strategy)
        connection.execute(
            """
            INSERT INTO research_topics (
                topic_id, topic_name_cn, topic_name_en, category, data_category,
                description, source, created_at, active
            ) VALUES (?, ?, ?, 'legacy_strategy', NULL, ?, 'legacy_backfill', ?, 1)
            ON CONFLICT(topic_id) DO NOTHING
            """,
            (
                topic_id,
                strategy,
                strategy,
                f"Backfilled legacy generation strategy: {strategy}",
                updated_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO topic_stats (
                topic_id, total_generated, total_simulated, total_passed_gate,
                total_submitted, pass_rate, avg_sharpe, avg_fitness,
                avg_self_corr, sampling_weight, last_updated
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, NULL, 1.0, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                total_generated=excluded.total_generated,
                total_simulated=excluded.total_simulated,
                total_passed_gate=excluded.total_passed_gate,
                pass_rate=excluded.pass_rate,
                avg_sharpe=excluded.avg_sharpe,
                avg_fitness=excluded.avg_fitness,
                last_updated=excluded.last_updated
            """,
            (
                topic_id,
                len(expression_ids),
                stats.count,
                stats.passed_count,
                stats.pass_rate,
                stats.avg_sharpe,
                stats.avg_fitness,
                updated_at,
            ),
        )


def backfill_csvs(
    database: str | Path, sources: Iterable[str | Path]
) -> BackfillSummary:
    database_path = Path(database).expanduser().resolve()
    source_paths = [Path(source).expanduser().resolve() for source in sources]
    SqliteRunLog(database_path).initialize_schema()

    summary = BackfillSummary()
    run_created_at = _utc_now()
    connection = sqlite3.connect(str(database_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        known_normalized = {
            str(row[0])
            for row in connection.execute("SELECT normalized_text FROM expressions")
            if row[0]
        }

        for source_path in source_paths:
            for row in _iter_csv(source_path):
                summary.rows_scanned += 1
                expression_text = _text(row, "expression")
                if not expression_text:
                    summary.blank_expressions += 1
                    continue
                normalized_text = _normalized_expression(expression_text)
                if not normalized_text:
                    summary.blank_expressions += 1
                    continue
                if normalized_text in known_normalized:
                    summary.duplicates_skipped += 1
                    continue

                expression_id = _expression_id(normalized_text)
                strategy = _text(row, "family", default="legacy_unknown")
                created_at = _text(row, "utc_iso", default=run_created_at)
                sharpe = _float(row, "sharpe")
                fitness = _float(row, "fitness")
                turnover = _float(row, "turnover")

                connection.execute(
                    """
                    INSERT INTO expressions (
                        expression_id, expression_text, normalized_text, structure_sig,
                        hypothesis_id, parent_expression_id, generation_strategy,
                        generation_layer, embedding, created_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?, 'L4', NULL, ?)
                    """,
                    (
                        expression_id,
                        expression_text,
                        normalized_text,
                        _structure_signature(expression_text),
                        strategy,
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO simulation_runs (
                        utc_iso, alpha_id, expression, status, queue_status,
                        sharpe, fitness, turnover, fail_reason, expression_id,
                        region, universe, neutralization, decay, delay, correlation_max
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        _text(row, "alpha_id"),
                        expression_text,
                        _text(row, "status"),
                        _text(row, "queue_status"),
                        sharpe,
                        fitness,
                        turnover,
                        _text(row, "failure reasons", "failure_reasons", "fail_reason"),
                        expression_id,
                        _text(row, "region"),
                        _text(row, "universe"),
                        _text(row, "neutralization"),
                        _int(row, "decay"),
                        _int(row, "delay"),
                        _float(row, "correlation_max"),
                    ),
                )
                known_normalized.add(normalized_text)
                summary.rows_imported += 1
                observed = {
                    _text(row, "status").lower(),
                    _text(row, "queue_status").lower(),
                }
                summary.by_strategy.setdefault(strategy, StrategyStats()).add(
                    sharpe,
                    fitness,
                    passed=bool(observed & {"pass", "passed", "submitted"}),
                )

        _refresh_legacy_topic_stats(connection, run_created_at)
        connection.commit()
        return summary
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _average_text(value: float | None) -> str:
    return f"{value:.6f}" if value is not None else "n/a"


def format_summary(summary: BackfillSummary) -> str:
    lines = [
        (
            "[backfill] "
            f"scanned={summary.rows_scanned} imported={summary.rows_imported} "
            f"duplicates={summary.duplicates_skipped} blanks={summary.blank_expressions}"
        )
    ]
    for strategy in sorted(summary.by_strategy):
        stats = summary.by_strategy[strategy]
        lines.append(
            f"[backfill] {strategy}: count={stats.count} "
            f"pass_rate={stats.pass_rate:.6f} "
            f"avg_sharpe={_average_text(stats.avg_sharpe)} "
            f"avg_fitness={_average_text(stats.avg_fitness)}"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill legacy alpha CSVs into Research Memory"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        dest="sources",
        help="CSV source; repeat for multiple files (defaults to the two repository legacy files).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = tuple(args.sources) if args.sources else DEFAULT_SOURCES
    summary = backfill_csvs(args.database, sources)
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
