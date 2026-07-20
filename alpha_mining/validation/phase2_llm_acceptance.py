"""Generate and atomically persist a human-review batch of L2 hypotheses."""

from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from alpha_mining.generator.hypothesis import (
    EmbeddingClient,
    GeneratedHypothesis,
    HypothesisGenerator,
    StructuredLLM,
)
from alpha_mining.llm import create_runtime_providers


@dataclass(frozen=True)
class AcceptanceResult:
    generated_count: int
    data_categories: tuple[str, ...]
    report: Path


def _active_topic_schedule(database: Path, count: int) -> list[tuple[str, str]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT topic_id, data_category
            FROM research_topics
            WHERE active = 1
              AND data_category IS NOT NULL
              AND TRIM(data_category) <> ''
            ORDER BY data_category, topic_id
            """
        ).fetchall()
    by_category: dict[str, list[str]] = {}
    for topic_id, category in rows:
        by_category.setdefault(str(category), []).append(str(topic_id))
    if len(by_category) < 3:
        raise ValueError("acceptance requires at least 3 active data categories")

    categories = sorted(by_category)
    schedule: list[tuple[str, str]] = []
    category_visits = {category: 0 for category in categories}
    for index in range(count):
        category = categories[index % len(categories)]
        topics = by_category[category]
        visit = category_visits[category]
        schedule.append((topics[visit % len(topics)], category))
        category_visits[category] = visit + 1
    return schedule


def _backup_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _protected_database_paths(database: Path) -> set[Path]:
    return {
        database.resolve(),
        database.with_name(f"{database.name}-wal").resolve(),
        database.with_name(f"{database.name}-shm").resolve(),
        database.with_name(f"{database.name}-journal").resolve(),
    }


def _validate_report_path(database: Path, report: Path) -> None:
    if report.resolve() in _protected_database_paths(database):
        raise ValueError("report path must not target database or SQLite sidecar")


def _remove_temporary_database(database: Path) -> None:
    # sqlite3 connection context managers do not close connections. Collect any
    # unreachable connection cycles before unlinking on Windows.
    gc.collect()
    for path in (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        database.with_name(f"{database.name}-journal"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _generated_rows(
    database: Path,
    generated: Sequence[GeneratedHypothesis],
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    with sqlite3.connect(database) as connection:
        for item in generated:
            row = connection.execute(
                """
                SELECT h.hypothesis_id, h.topic_id, h.statement_cn, h.statement_en,
                       h.mechanism, h.horizon, h.embedding, h.created_at, h.llm_model,
                       h.status, t.data_category
                FROM hypotheses h
                JOIN research_topics t ON t.topic_id = h.topic_id
                WHERE h.hypothesis_id = ?
                """,
                (item.hypothesis_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"generated hypothesis disappeared: {item.hypothesis_id}"
                )
            rows.append(row)
    return rows


def _write_report_temp(
    report: Path,
    rows: Sequence[tuple[Any, ...]],
    generated: Sequence[GeneratedHypothesis],
) -> Path:
    if len(rows) != len(generated):
        raise RuntimeError("report rows and generated hypotheses are out of sync")
    report.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_count": len(rows),
        "data_categories": sorted({str(row[10]) for row in rows}),
        "persistence_note": (
            "expected_direction 和 candidate_data_concepts 仅用于人工抽查；"
            "当前权威 SQLite schema 尚未落库。"
        ),
        "hypotheses": [
            {
                "statement": row[2],
                "mechanism": row[4],
                "horizon": row[5],
                "expected_direction": item.draft.expected_direction,
                "candidate_data_concepts": list(item.draft.candidate_data_concepts),
                "topic": row[1],
                "category": row[10],
            }
            for row, item in zip(rows, generated)
        ],
    }
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{report.name}.",
        suffix=".tmp",
        dir=report.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return temporary


def _commit_rows_and_report(
    database: Path,
    report: Path,
    report_temp: Path,
    rows: Sequence[tuple[Any, ...]],
) -> None:
    _validate_report_path(database, report)
    backup_report = report.with_name(f".{report.name}.{uuid.uuid4().hex}.bak")
    report_was_backed_up = False
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            topic = connection.execute(
                "SELECT data_category FROM research_topics WHERE topic_id=? AND active=1",
                (row[1],),
            ).fetchone()
            if topic is None or str(topic[0]) != str(row[10]):
                raise RuntimeError(f"active topic changed during acceptance: {row[1]}")
        connection.executemany(
            """
            INSERT INTO hypotheses (
                hypothesis_id, topic_id, statement_cn, statement_en, mechanism,
                horizon, embedding, created_at, llm_model, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row[:10] for row in rows],
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        try:
            report_temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        connection.close()

    # SQLite is committed before the separate report publish begins.
    try:
        if report.exists():
            os.replace(report, backup_report)
            report_was_backed_up = True
        os.replace(report_temp, report)
    except BaseException:
        try:
            report_temp.unlink(missing_ok=True)
        except OSError:
            pass
        if report_was_backed_up:
            if not report.exists():
                os.replace(backup_report, report)
            else:
                try:
                    backup_report.unlink(missing_ok=True)
                except OSError:
                    pass
        raise
    try:
        backup_report.unlink(missing_ok=True)
    except OSError:
        pass


def run_acceptance(
    database: str | Path = "research_memory.sqlite",
    *,
    count: int = 20,
    report: str | Path = "work/phase2_llm_acceptance.json",
    llm: StructuredLLM | None = None,
    embedder: EmbeddingClient | None = None,
    model_id: str | None = None,
) -> AcceptanceResult:
    """Generate off-target, commit SQLite rows, then publish the review report."""
    if count < 3:
        raise ValueError("count must be at least 3 to verify category coverage")
    target = Path(database).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"research memory database not found: {target}")
    report_path = Path(report).expanduser().resolve()
    _validate_report_path(target, report_path)
    schedule = _active_topic_schedule(target, count)

    providers = None
    if llm is None or embedder is None:
        providers = create_runtime_providers()
        llm = llm or providers.llm
        embedder = embedder or providers.embedder
        model_id = model_id or providers.llm.model_id
    resolved_model_id = (model_id or "injected-structured-llm").strip()
    if not resolved_model_id:
        raise ValueError("model_id must not be empty")

    temporary_handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.phase2-",
        suffix=".sqlite",
        dir=target.parent,
        delete=False,
    )
    temporary_database = Path(temporary_handle.name)
    temporary_handle.close()
    report_temp: Path | None = None
    try:
        _backup_database(target, temporary_database)
        generator = HypothesisGenerator(
            temporary_database,
            llm=llm,
            embedder=embedder,
            model_id=resolved_model_id,
        )
        generated = [generator.generate(topic_id) for topic_id, _ in schedule]
        rows = _generated_rows(temporary_database, generated)
        if len(rows) != count:
            raise RuntimeError(
                f"acceptance generated {len(rows)} rows instead of {count}"
            )
        categories = tuple(sorted({str(row[10]) for row in rows}))
        if len(categories) < 3:
            raise RuntimeError(
                "acceptance output did not cover at least 3 data categories"
            )
        report_temp = _write_report_temp(report_path, rows, generated)
        _commit_rows_and_report(target, report_path, report_temp, rows)
        report_temp = None
        return AcceptanceResult(
            generated_count=len(rows),
            data_categories=categories,
            report=report_path,
        )
    finally:
        try:
            _remove_temporary_database(temporary_database)
        except OSError:
            pass
        if report_temp is not None:
            try:
                report_temp.unlink(missing_ok=True)
            except OSError:
                pass
        if providers is not None:
            try:
                providers.close()
            except Exception:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a 20-item Phase 2 hypothesis batch for human review."
    )
    parser.add_argument("--database", default="research_memory.sqlite")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--report", default="work/phase2_llm_acceptance.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_acceptance(args.database, count=args.count, report=args.report)
    print(
        json.dumps(
            {
                "generated_count": result.generated_count,
                "data_categories": result.data_categories,
                "report": str(result.report),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
