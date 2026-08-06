"""Authoritative candidate outcome feedback store.

One authoritative row is kept per simulation request. Repeated terminal writes are
idempotent. A provisional ``WAITING_CHECKS`` observation may be upgraded once
when the platform later returns the mandatory checks for the same request.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from alpha_mining.domain.expression_normalization import operator_topology
from alpha_mining.storage.migrations import migrate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_VALID_OUTCOMES = frozenset(("PASS", "READY_TO_SUBMIT", "WAITING_CHECKS", "NEAR_PASS", "FAR_FAIL", "FAILED", "UNKNOWN"))


class CandidateFeedbackStore:
    """Persist simulation outcomes for feedback-driven candidate generation."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        migrate(self.database)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS candidate_outcomes (
                    request_hash TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL DEFAULT '',
                    expression TEXT NOT NULL DEFAULT '',
                    topic_id TEXT NOT NULL DEFAULT '',
                    hypothesis_id TEXT NOT NULL DEFAULT '',
                    research_family TEXT NOT NULL DEFAULT '',
                    strategy_family TEXT NOT NULL DEFAULT '',
                    mechanism TEXT NOT NULL DEFAULT '',
                    dataset TEXT NOT NULL DEFAULT '',
                    parent_template TEXT NOT NULL DEFAULT '',
                    exact_hash TEXT NOT NULL DEFAULT '',
                    parameter_skeleton TEXT NOT NULL DEFAULT '',
                    field_skeleton TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL,
                    sharpe REAL,
                    fitness REAL,
                    turnover REAL,
                    checks_json TEXT NOT NULL DEFAULT '[]',
                    error_category TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                )"""
            )
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(candidate_outcomes)")}
            if "expression" not in columns:
                con.execute("ALTER TABLE candidate_outcomes ADD COLUMN expression TEXT NOT NULL DEFAULT ''")
            con.execute("CREATE INDEX IF NOT EXISTS idx_co_topic ON candidate_outcomes(topic_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_co_family ON candidate_outcomes(strategy_family)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_co_skeleton ON candidate_outcomes(field_skeleton)")

    def record(
        self,
        request_hash: str,
        outcome: str,
        *,
        candidate_id: str = "",
        expression: str = "",
        sharpe: float | None = None,
        fitness: float | None = None,
        turnover: float | None = None,
        field_skeleton: str = "",
        strategy_family: str = "",
        topic_id: str = "",
        hypothesis_id: str = "",
        research_family: str = "",
        mechanism: str = "",
        dataset: str = "",
        parent_template: str = "",
        exact_hash: str = "",
        parameter_skeleton: str = "",
        checks: list[Any] | None = None,
        error_category: str = "",
        error_message: str = "",
        quality_status: str = "",
        quality_reasons: list[str] | tuple[str, ...] | None = None,
        self_correlation: str = "",
        prod_correlation: str = "",
        knowledge_refs: list[str] | tuple[str, ...] | None = None,
        parent_candidate_id: str = "",
        repair_action: str = "",
        operator_topology: str = "",
        region: str = "",
        universe_name: str = "",
        delay: str | int = "",
        knowledge_usage_mode: str = "NONE",
        context_refs: list[str] | tuple[str, ...] | None = None,
        knowledge_context_hash: str = "",
        degraded: bool = False,
    ) -> None:
        if outcome not in _VALID_OUTCOMES:
            raise ValueError(f"Invalid outcome {outcome!r}; must be one of {sorted(_VALID_OUTCOMES)}")
        if not str(request_hash or "").strip():
            raise ValueError("request_hash is required")
        now = _utc_now()
        values = (
            str(request_hash), str(candidate_id), str(expression), str(topic_id), str(hypothesis_id),
            str(research_family), str(strategy_family), str(mechanism), str(dataset), str(parent_template),
            str(exact_hash), str(parameter_skeleton), str(field_skeleton), str(outcome), sharpe, fitness, turnover,
            json.dumps(checks or []), str(error_category), str(error_message), now, str(quality_status or outcome),
            json.dumps(list(quality_reasons or [])), str(self_correlation), str(prod_correlation),
            json.dumps(list(knowledge_refs or [])), str(parent_candidate_id), str(repair_action),
            str(operator_topology), str(region), str(universe_name), str(delay), str(knowledge_usage_mode),
            json.dumps(list(context_refs or [])), str(knowledge_context_hash), int(bool(degraded)),
        )
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT INTO candidate_outcomes
                   (request_hash,candidate_id,expression,topic_id,hypothesis_id,research_family,
                    strategy_family,mechanism,dataset,parent_template,exact_hash,
                    parameter_skeleton,field_skeleton,outcome,sharpe,fitness,turnover,
                    checks_json,error_category,error_message,observed_at,quality_status,
                    quality_reasons_json,self_correlation,prod_correlation,knowledge_refs_json,
                    parent_candidate_id,repair_action,operator_topology,region,universe_name,delay,
                    knowledge_usage_mode,context_refs_json,knowledge_context_hash,degraded)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(request_hash) DO UPDATE SET
                     candidate_id=excluded.candidate_id,
                     expression=excluded.expression,
                     topic_id=excluded.topic_id,
                     hypothesis_id=excluded.hypothesis_id,
                     research_family=excluded.research_family,
                     strategy_family=excluded.strategy_family,
                     mechanism=excluded.mechanism,
                     dataset=excluded.dataset,
                     parent_template=excluded.parent_template,
                     exact_hash=excluded.exact_hash,
                     parameter_skeleton=excluded.parameter_skeleton,
                     field_skeleton=excluded.field_skeleton,
                     outcome=excluded.outcome,
                     sharpe=excluded.sharpe,
                     fitness=excluded.fitness,
                     turnover=excluded.turnover,
                     checks_json=excluded.checks_json,
                     error_category=excluded.error_category,
                     error_message=excluded.error_message,
                     observed_at=excluded.observed_at,
                     quality_status=excluded.quality_status,
                     quality_reasons_json=excluded.quality_reasons_json,
                     self_correlation=excluded.self_correlation,
                     prod_correlation=excluded.prod_correlation,
                     knowledge_refs_json=excluded.knowledge_refs_json,
                     parent_candidate_id=excluded.parent_candidate_id,
                     repair_action=excluded.repair_action,
                     operator_topology=excluded.operator_topology,
                     region=excluded.region,
                     universe_name=excluded.universe_name,
                     delay=excluded.delay,
                     knowledge_usage_mode=excluded.knowledge_usage_mode,
                     context_refs_json=excluded.context_refs_json,
                     knowledge_context_hash=excluded.knowledge_context_hash,
                     degraded=excluded.degraded
                   WHERE candidate_outcomes.outcome='WAITING_CHECKS'
                     AND excluded.outcome<>'WAITING_CHECKS'""",
                values,
            )

    def outcomes_for_request(self, request_hash: str) -> str | None:
        with sqlite3.connect(self.database) as con:
            row = con.execute("SELECT outcome FROM candidate_outcomes WHERE request_hash=?", (request_hash,)).fetchone()
        return row[0] if row else None

    def family_pass_rate(self, strategy_family: str) -> float:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN outcome IN ('PASS','READY_TO_SUBMIT') THEN 1 ELSE 0 END)
                   FROM candidate_outcomes WHERE strategy_family=?""",
                (strategy_family,),
            ).fetchone()
        if not row or not row[0]:
            return 0.0
        return float(row[1] or 0) / float(row[0])


def record_candidate_outcome(
    feedback: CandidateFeedbackStore,
    proposal: Any,
    request_hash: str,
    *,
    outcome: str,
    result: Any | None,
    quality_reasons: tuple[str, ...] | list[str] = (),
    error_category: str = "",
    error_message: str = "",
    settings: Mapping[str, Any] | None = None,
) -> None:
    """Persist one active-workflow result with the proposal provenance intact."""

    metrics = getattr(result, "metrics", {}) or {}
    checks = getattr(result, "checks", []) or []
    statuses = {
        str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "").upper()
        for item in checks
        if isinstance(item, dict)
    }
    settings_map = dict(settings or {})
    expression = str(getattr(proposal, "expression", "") or "")
    feedback.record(
        request_hash or str(getattr(proposal, "exact_hash", "") or ""),
        outcome,
        candidate_id=str(getattr(proposal, "candidate_id", "") or ""),
        expression=expression,
        topic_id=str(getattr(proposal, "topic_id", "") or ""),
        hypothesis_id=str(getattr(proposal, "hypothesis_id", "") or ""),
        research_family=str(getattr(proposal, "research_family", "") or ""),
        strategy_family=str(getattr(proposal, "strategy_family", "") or ""),
        mechanism=str(getattr(proposal, "mechanism", "") or ""),
        dataset=str(getattr(proposal, "dataset", "") or ""),
        parent_template=str(getattr(proposal, "parent_template", "") or ""),
        exact_hash=str(getattr(proposal, "exact_hash", "") or ""),
        parameter_skeleton=str(getattr(proposal, "parameter_skeleton", "") or ""),
        field_skeleton=str(getattr(proposal, "field_skeleton", "") or ""),
        sharpe=metrics.get("sharpe"),
        fitness=metrics.get("fitness"),
        turnover=metrics.get("turnover"),
        checks=checks,
        error_category=error_category,
        error_message=error_message,
        quality_status=outcome,
        quality_reasons=quality_reasons,
        self_correlation=statuses.get("SELF_CORRELATION", ""),
        prod_correlation=statuses.get("PROD_CORRELATION", statuses.get("PRODUCTION_CORRELATION", "")),
        knowledge_refs=tuple(getattr(proposal, "knowledge_refs", ()) or ()),
        knowledge_usage_mode=str(getattr(proposal, "knowledge_usage_mode", "NONE") or "NONE"),
        context_refs=tuple(getattr(proposal, "context_refs", ()) or ()),
        knowledge_context_hash=str(getattr(proposal, "knowledge_context_hash", "") or ""),
        degraded=bool(getattr(proposal, "degraded", False)),
        parent_candidate_id=str(getattr(proposal, "parent_candidate_id", "") or ""),
        repair_action=str(getattr(proposal, "repair_origin", "") or ""),
        operator_topology=operator_topology(expression),
        region=str(settings_map.get("region") or settings_map.get("regionName") or "USA"),
        universe_name=str(settings_map.get("universe") or settings_map.get("universeName") or "TOP3000"),
        delay=settings_map.get("delay", "1"),
    )
