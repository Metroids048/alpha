"""Read-only guard evaluation for triaged legacy candidates."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from alpha_mining.platform.gates import (
    GateRegistry,
    GateScope,
    MissingGateSnapshot,
    StaleGateSnapshot,
)
from alpha_mining.policy.consultant_policy import ConsultantPolicy
from alpha_mining.correlation.service import CorrelationService
from .guard import CandidateContext, SubmissionGuard
from .judge import quality_buffer_pass


def _checks(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for pool in (payload, payload.get("is"), payload.get("summary")):
        if isinstance(pool, dict) and isinstance(pool.get("checks"), list):
            return [item for item in pool["checks"] if isinstance(item, dict)]
    return []


def evaluate_triaged_candidates(
    database: str | Path, *, policy: ConsultantPolicy | None = None
) -> dict:
    policy = policy or ConsultantPolicy()
    guard = SubmissionGuard()
    registry = GateRegistry(database, freshness_hours=policy.gate_freshness_hours)
    reasons: Counter[str] = Counter()
    total = allowed = 0
    with sqlite3.connect(database) as con:
        rows = con.execute("""SELECT l.legacy_id,l.alpha_id,l.settings_json,l.metrics_json,l.checks_json,f.unit_warnings_json
            FROM legacy_triage_results t JOIN legacy_alphas l ON l.legacy_id=t.legacy_id
            JOIN alpha_expression_features f ON f.canonical_id=l.canonical_id
            WHERE t.classification='RECHECK' ORDER BY l.legacy_id""")
        for (
            legacy_id,
            alpha_id,
            settings_json,
            metrics_json,
            checks_json,
            warnings_json,
        ) in rows:
            total += 1
            settings = json.loads(settings_json or "{}")
            metrics = json.loads(metrics_json or "{}")
            checks = _checks(json.loads(checks_json or "[]"))
            check_metric_names = {
                "LOW_SUB_UNIVERSE_SHARPE": "sub_universe_sharpe",
                "SELF_CORRELATION": "self_correlation",
                "PROD_CORRELATION": "production_correlation",
                "PRODUCTION_CORRELATION": "production_correlation",
            }
            for check in checks:
                name = str(check.get("name") or "").upper()
                metric_name = check_metric_names.get(name)
                if metric_name and check.get("value") is not None:
                    metrics[metric_name] = check.get("value")
            scope = GateScope(
                region=settings.get("region", "*"),
                universe=settings.get("universe", "*"),
                delay=settings.get("delay", "*"),
                alpha_type=settings.get("type")
                or settings.get("alpha_type")
                or "REGULAR",
                theme_id=settings.get("theme_id", "*"),
                pyramid_id=settings.get("pyramid_id", "*"),
            )
            gates = {}
            fresh = True
            required_gate_names = (
                "LOW_SHARPE",
                "LOW_FITNESS",
                "LOW_TURNOVER",
                "HIGH_TURNOVER",
                "LOW_SUB_UNIVERSE_SHARPE",
                "SELF_CORRELATION",
                "PROD_CORRELATION",
            )
            for gate_name in required_gate_names:
                try:
                    snapshot = registry.require_fresh(scope, gate_name)
                except (MissingGateSnapshot, StaleGateSnapshot):
                    fresh = False
                    continue
                gates[gate_name] = (snapshot.limit, snapshot.direction)
            quality, _ = quality_buffer_pass(metrics, gates, policy=policy)
            candidate_returns = [
                (str(date), float(value))
                for date, value in con.execute(
                    "SELECT date,daily_return FROM alpha_daily_returns WHERE expression_id=? ORDER BY date",
                    (legacy_id,),
                )
            ]
            self_gate = gates.get("SELF_CORRELATION")
            local_status = "INSUFFICIENT_HISTORY"
            if self_gate and candidate_returns:
                internal_limit = policy.internal_limit(
                    live_limit=self_gate[0],
                    direction=self_gate[1],
                    gate_name="SELF_CORRELATION",
                )
                service = CorrelationService(
                    min_overlap=policy.min_correlation_overlap,
                    internal_limit=internal_limit,
                )
                compared = []
                reference_ids = [
                    row[0]
                    for row in con.execute(
                        "SELECT DISTINCT expression_id FROM alpha_daily_returns WHERE expression_id<>? ORDER BY expression_id",
                        (legacy_id,),
                    )
                ]
                for reference_id in reference_ids:
                    reference = [
                        (str(date), float(value))
                        for date, value in con.execute(
                            "SELECT date,daily_return FROM alpha_daily_returns WHERE expression_id=? ORDER BY date",
                            (reference_id,),
                        )
                    ]
                    compared.append(service.compare(candidate_returns, reference))
                if any(result.status == "FAIL" for result in compared):
                    local_status = "FAIL"
                elif any(result.status == "PASS" for result in compared):
                    local_status = "PASS"
            context = CandidateContext(
                alpha_id=str(alpha_id),
                expression_id=str(legacy_id),
                checks=checks,
                gate_snapshots_fresh=fresh,
                quality_buffer_pass=quality,
                local_correlation_status=local_status,
                unit_warnings=tuple(json.loads(warnings_json or "[]")),
                concentration_failure=any(
                    str(check.get("name") or "").upper() == "CONCENTRATED_WEIGHT"
                    and str(check.get("result") or "").upper() != "PASS"
                    for check in checks
                ),
                mandatory_checks=required_gate_names,
            )
            decision = guard.evaluate(context)
            if decision.allowed:
                allowed += 1
            else:
                for reason in decision.reasons:
                    reasons[reason] += 1
    return {
        "candidates": total,
        "allowed": allowed,
        "blocked": total - allowed,
        "blocked_reasons": dict(reasons.most_common()),
    }
