"""Local-only submission observation records for platform feedback."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from alpha_mining.filter.repair import RepairEngine
from alpha_mining.integration.phase4 import expression_id_for
from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.submitter.description import (
    DescriptionDraft,
    StructuredLLM,
    generate_description,
)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(checks: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_json(checks).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SubmissionObservation:
    observation_id: str
    failure_categories: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    description_text: str | None
    description_source: str | None


@dataclass(frozen=True)
class ObservationReplaySummary:
    rows_scanned: int
    rows_observed: int
    descriptions_generated: int
    failure_category_counts: dict[str, int]


class SubmissionObservationService:
    """Persist observations only; this class has no HTTP or queue dependencies."""

    def __init__(
        self,
        database: SqliteRunLog,
        *,
        llm_factory: Callable[[], StructuredLLM] | None = None,
        description_limit: int = 20,
    ) -> None:
        if not database.path:
            raise ValueError("submission observation requires a SQLite database path")
        self.database = database
        self.database.initialize_schema()
        self.llm_factory = llm_factory
        self.description_limit = max(0, int(description_limit))
        self._description_count = 0
        self._repair = RepairEngine()

    def _draft(self, expression: str, family: str, source: str) -> DescriptionDraft:
        llm: StructuredLLM | None = None
        if self.llm_factory is not None:
            try:
                llm = self.llm_factory()
            except Exception:
                llm = None
        try:
            return generate_description(
                expression, llm=llm, family=family, source=source
            )
        finally:
            close = getattr(llm, "close", None)
            if callable(close):
                close()

    def observe(
        self,
        *,
        alpha_id: str | None,
        expression: str,
        checks: list[dict[str, Any]] | None,
        metrics: dict[str, Any] | None,
        queue_status: str,
        check_passed: bool | None,
        failure_detail: str,
        family: str = "",
        source: str = "",
    ) -> SubmissionObservation:
        expression_id = expression_id_for(expression)
        normalized_alpha_id = str(alpha_id or "").strip()
        normalized_checks = [
            dict(check) for check in (checks or []) if isinstance(check, dict)
        ]
        check_digest = _digest(normalized_checks)
        observation_id = hashlib.sha256(
            f"{expression_id}|{normalized_alpha_id}|{check_digest}".encode("utf-8")
        ).hexdigest()
        categories = (
            tuple(self._repair.classify_all(failure_detail))
            if check_passed is False
            else ()
        )
        actions = tuple(
            self._repair.repair(expression, category).repair_strategy
            for category in categories
        )
        draft: DescriptionDraft | None = None
        if (
            str(queue_status) == "ready"
            and self._description_count < self.description_limit
        ):
            draft = self._draft(expression, family, source)
            self._description_count += 1

        with sqlite3.connect(str(self.database.path)) as connection:
            connection.execute(
                """
                INSERT INTO submission_observations (
                    observation_id, expression_id, alpha_id, check_digest, check_passed,
                    queue_status, metrics_json, checks_json, failure_categories_json,
                    recommended_actions_json, description_text, description_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(expression_id, alpha_id, check_digest) DO NOTHING
                """,
                (
                    observation_id,
                    expression_id,
                    normalized_alpha_id,
                    check_digest,
                    None if check_passed is None else int(check_passed),
                    str(queue_status),
                    _json(metrics or {}),
                    _json(normalized_checks),
                    _json(categories),
                    _json(actions),
                    draft.text if draft else None,
                    draft.source if draft else None,
                    _utc_now(),
                ),
            )
            row = connection.execute(
                """
                SELECT observation_id, failure_categories_json, recommended_actions_json,
                       description_text, description_source
                FROM submission_observations
                WHERE expression_id=? AND alpha_id=? AND check_digest=?
                """,
                (expression_id, normalized_alpha_id, check_digest),
            ).fetchone()
        return SubmissionObservation(
            observation_id=str(row[0]),
            failure_categories=tuple(json.loads(row[1])),
            recommended_actions=tuple(json.loads(row[2])),
            description_text=row[3],
            description_source=row[4],
        )

    def fetch_description(self, expression_id: str, alpha_id: str) -> str | None:
        """Return the first non-empty description_text stored for this expression/alpha pair."""
        normalized_alpha_id = str(alpha_id or "").strip()
        with sqlite3.connect(str(self.database.path)) as connection:
            row = connection.execute(
                """
                SELECT description_text FROM submission_observations
                WHERE expression_id=? AND alpha_id=? AND description_text IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (expression_id, normalized_alpha_id),
            ).fetchone()
        if row:
            return row[0]
        # Fall back: any description for this expression regardless of alpha_id
        with sqlite3.connect(str(self.database.path)) as connection:
            row = connection.execute(
                """
                SELECT description_text FROM submission_observations
                WHERE expression_id=? AND description_text IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (expression_id,),
            ).fetchone()
        return row[0] if row else None


def _parse_bool(value: object) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _checks_from_json(value: object) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    details = payload.get("is") if isinstance(payload.get("is"), dict) else payload
    checks = details.get("checks") if isinstance(details, dict) else None
    return (
        [dict(check) for check in checks if isinstance(check, dict)]
        if isinstance(checks, list)
        else []
    )


def observe_feedback_csv(
    database: SqliteRunLog,
    source: str | Path,
    *,
    llm_factory: Callable[[], StructuredLLM] | None = None,
    description_limit: int = 20,
) -> ObservationReplaySummary:
    """Replay an existing feedback ledger into local observation records only."""
    service = SubmissionObservationService(
        database,
        llm_factory=llm_factory,
        description_limit=description_limit,
    )
    scanned = 0
    observed = 0
    descriptions = 0
    category_counts: Counter[str] = Counter()
    with Path(source).open(
        "r", encoding="utf-8-sig", newline="", errors="ignore"
    ) as handle:
        for row in csv.DictReader(handle):
            scanned += 1
            expression = str(row.get("expression") or "").strip()
            if not expression:
                continue
            result = service.observe(
                alpha_id=row.get("alpha_id"),
                expression=expression,
                checks=_checks_from_json(row.get("platform_check_json")),
                metrics={
                    "sharpe": row.get("Sharpe"),
                    "fitness": row.get("Fitness"),
                    "turnover": row.get("Turnover"),
                },
                queue_status=str(row.get("queue_status") or ""),
                check_passed=_parse_bool(row.get("check_passed")),
                failure_detail=str(row.get("Failure Reasons") or ""),
                family=str(row.get("family") or ""),
                source=str(row.get("source") or ""),
            )
            observed += 1
            descriptions += int(result.description_text is not None)
            category_counts.update(result.failure_categories)
    return ObservationReplaySummary(
        scanned, observed, descriptions, dict(sorted(category_counts.items()))
    )
