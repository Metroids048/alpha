"""Offline Phase 4 bridge between pipeline records and Research Memory."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from alpha_mining.filter.repair import RepairEngine, persist_repair
from alpha_mining.mutate.tree_mutation import MutationEngine, persist_mutation
from alpha_mining.storage.sqlite_store import SqliteRunLog


_EXPRESSION_NAMESPACE = uuid.UUID("92fd2d5d-61a1-477a-aa3c-a22cc4e4d3d2")


def _normalized_expression(expression: str) -> str:
    return re.sub(r"\s+", "", str(expression or "")).lower()


def expression_id_for(expression: str) -> str:
    """Return a stable Research Memory identity for an expression text."""
    return str(uuid.uuid5(_EXPRESSION_NAMESPACE, _normalized_expression(expression)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_expression(
    db: SqliteRunLog,
    expression: str,
    *,
    parent_expression_id: str | None,
    generation_strategy: str,
    generation_layer: str,
) -> str:
    expression_id = expression_id_for(expression)
    if not db.path:
        return expression_id
    with sqlite3.connect(str(db.path)) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO expressions "
            "(expression_id, expression_text, normalized_text, structure_sig, "
            " parent_expression_id, generation_strategy, generation_layer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                expression_id,
                expression,
                _normalized_expression(expression),
                None,
                parent_expression_id,
                generation_strategy,
                generation_layer,
                _utc_now(),
            ),
        )
    return expression_id


@dataclass(frozen=True)
class MutationCandidate:
    expression: str
    expression_id: str
    parent_expression_id: str
    axis: str
    detail: str
    settings: dict[str, Any]
    metrics: dict[str, Any]

    def as_hopeful_candidate_record(self) -> dict[str, Any]:
        """Return the expression/settings/meta shape consumed by HopefulQueue entries."""
        return {
            "expression": self.expression,
            "settings": dict(self.settings),
            "metrics": dict(self.metrics),
            "meta": {
                "family": "near_pass_variant_tree_mutation",
                "source": "phase4_tree_mutation",
                "parent_expression_id": self.parent_expression_id,
                "expression_id": self.expression_id,
                "mutation_axis": self.axis,
                "mutation_detail": self.detail,
            },
        }


class Phase4ResearchMemoryBridge:
    """Persist and expose offline L5/L6 actions at existing pipeline boundaries."""

    def __init__(self, db: SqliteRunLog) -> None:
        self.db = db
        self.mutations = MutationEngine()
        self.repairs = RepairEngine()
        if db.path:
            db.initialize_schema()

    def mutate_near_pass_records(
        self,
        records: Iterable[dict[str, Any]],
        *,
        validate: Callable[[str], tuple[bool, str]],
        existing_expressions: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Create valid, deduplicated L5 children from already eligible near-pass records."""
        records = list(records)
        seen = {
            _normalized_expression(expression) for expression in existing_expressions
        }
        seen.update(
            _normalized_expression(str(record.get("expression") or ""))
            for record in records
        )
        peer_expressions = [str(record.get("expression") or "") for record in records]
        candidates: list[dict[str, Any]] = []

        for record in records:
            parent_expression = str(record.get("expression") or "").strip()
            if not parent_expression or record.get("eligible") is False:
                continue
            parent_id = _upsert_expression(
                self.db,
                parent_expression,
                parent_expression_id=None,
                generation_strategy="near_pass",
                generation_layer="L5",
            )
            settings = dict(record.get("settings") or {})
            metrics = dict(record.get("metrics") or {})
            if "sharpe" in record and "sharpe" not in metrics:
                metrics["sharpe"] = record["sharpe"]
            for mutation in self.mutations.mutate_all_axes(
                parent_expression,
                peer_exprs=peer_expressions,
                parent_expression_id=parent_id,
            ):
                child = mutation.mutated_expression
                identity = _normalized_expression(child)
                if not identity or identity in seen:
                    continue
                valid, _reason = validate(child)
                if not valid:
                    continue
                child_id = _upsert_expression(
                    self.db,
                    child,
                    parent_expression_id=parent_id,
                    generation_strategy="tree_mutation",
                    generation_layer="L5",
                )
                persist_mutation(
                    self.db,
                    parent_expression_id=parent_id,
                    child_expression_id=child_id,
                    axis=mutation.axis,
                    detail=mutation.detail,
                    mutation_id=mutation.mutation_id,
                )
                seen.add(identity)
                candidates.append(
                    MutationCandidate(
                        expression=child,
                        expression_id=child_id,
                        parent_expression_id=parent_id,
                        axis=mutation.axis,
                        detail=mutation.detail,
                        settings=settings,
                        metrics=metrics,
                    ).as_hopeful_candidate_record()
                )
        return candidates

    def record_feedback_result(
        self,
        *,
        expression: str,
        failure_detail: str,
        check_passed: bool | None,
        generation_strategy: str,
    ) -> None:
        """Persist a repair only for a failed result, then resolve prior repairs from later results."""
        expression = str(expression or "").strip()
        if not expression:
            return
        expression_id = _upsert_expression(
            self.db,
            expression,
            parent_expression_id=None,
            generation_strategy=generation_strategy or "pipeline_feedback",
            generation_layer="L6",
        )
        if not self.db.path:
            return
        if check_passed is not None:
            with sqlite3.connect(str(self.db.path)) as connection:
                connection.execute(
                    "UPDATE repairs SET success=? "
                    "WHERE resulting_expression_id=? AND success IS NULL",
                    (int(check_passed), expression_id),
                )
        if check_passed is not False:
            return
        for category in self.repairs.classify_all(failure_detail):
            repair = self.repairs.repair(expression, category)
            resulting_expression_id: str | None = None
            if repair.repaired_expression:
                resulting_expression_id = _upsert_expression(
                    self.db,
                    repair.repaired_expression,
                    parent_expression_id=expression_id,
                    generation_strategy=repair.repair_strategy,
                    generation_layer="L6",
                )
            persist_repair(
                self.db,
                expression_id=expression_id,
                failure_category=category,
                failure_detail=failure_detail,
                repair_strategy=repair.repair_strategy,
                resulting_expression_id=resulting_expression_id,
                success=None,
            )
