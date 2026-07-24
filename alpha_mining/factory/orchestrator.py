"""Authoritative sequential baseline-first generation and simulation cycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.domain.expression_normalization import behavior_signature, operator_topology
from alpha_mining.domain.operator_registry import BASE_VARS
from alpha_mining.description.pipeline import DescriptionPipeline
from alpha_mining.generator.baseline_first import BaselineOutcome, classify_baseline
from alpha_mining.generator.consultant_generator import ConsultantGenerator
from alpha_mining.integration.phase4 import expression_id_for
from alpha_mining.scheduler.arm_metrics import ArmDimensions, ResearchArmTracker
from alpha_mining.simulate.settings_optimizer import SettingsOptimizer
from alpha_mining.storage.migrations import migrate
from alpha_mining.storage.sqlite_store import SqliteRunLog


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SimulationResult:
    alpha_id: str
    status: str
    metrics: dict[str, float]
    checks: list[dict[str, Any]]
    raw: dict[str, Any]


class SimulationService(Protocol):
    def simulate(
        self, *, expression: str, settings: dict[str, Any], alpha_type: str = "REGULAR"
    ) -> SimulationResult: ...


@dataclass(frozen=True)
class ResearchSpec:
    hypothesis_id: str
    family: str
    mechanism: str
    horizon: str
    fields: tuple[str, ...]
    dataset: str
    fallback: bool = False


@dataclass(frozen=True)
class FactoryCycleSummary:
    generated: int
    simulated: int
    far_fail: int
    near_pass: int
    baseline_pass: int
    failed: int
    descriptions_validated: int = 0


class FactoryOrchestrator:
    def __init__(self, database: str | Path, simulation: SimulationService) -> None:
        self.database = Path(database)
        SqliteRunLog(self.database).initialize_schema()
        migrate(self.database)
        self.simulation = simulation
        self.generator = ConsultantGenerator()

    def _research_specs(self) -> list[ResearchSpec]:
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT h.hypothesis_id,COALESCE(t.category,'UNCLASSIFIED'),
                          COALESCE(h.mechanism,h.statement_en,h.statement_cn),
                          COALESCE(h.horizon,'medium'),m.data_field,COALESCE(m.dataset_id,'UNKNOWN')
                   FROM hypotheses h
                   JOIN research_topics t ON t.topic_id=h.topic_id
                   JOIN data_mappings m ON m.hypothesis_id=h.hypothesis_id
                   WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1
                   ORDER BY h.created_at,h.hypothesis_id,m.field_quality_score DESC,m.data_field"""
            ).fetchall()
        grouped: dict[str, ResearchSpec] = {}
        for row in rows:
            key = str(row[0])
            if key not in grouped:
                grouped[key] = ResearchSpec(
                    key, str(row[1]), str(row[2]), str(row[3]), (str(row[4]),), str(row[5])
                )
            else:
                current = grouped[key]
                grouped[key] = ResearchSpec(
                    current.hypothesis_id,
                    current.family,
                    current.mechanism,
                    current.horizon,
                    tuple(dict.fromkeys((*current.fields, str(row[4])))),
                    current.dataset,
                )
        if grouped:
            return list(grouped.values())
        fallback_fields = ("close", "volume", "returns", "vwap", "open", "high", "low", "adv20")
        return [
            ResearchSpec(
                f"fallback-{field}",
                "platform_base",
                f"relative {field} behavior",
                "short" if field in {"returns", "open", "close"} else "medium",
                (field,),
                "pv1",
                True,
            )
            for field in fallback_fields
        ]

    def _live_sharpe_threshold(self) -> float:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT limit_value FROM platform_gate_snapshots
                   WHERE gate_name='LOW_SHARPE' ORDER BY last_seen_at DESC,version DESC LIMIT 1"""
            ).fetchone()
        return float(row[0]) if row else 1.25

    def _ledger_sync_id(self) -> str:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT ledger_sync_id FROM factory_control WHERE singleton=1"
            ).fetchone()
        return str(row[0]).strip() if row and row[0] else ""

    def _prepare_description(
        self,
        *,
        spec: ResearchSpec,
        expression: str,
        settings: dict[str, Any],
        result: SimulationResult,
    ) -> bool:
        raw = result.raw if isinstance(result.raw, dict) else {}
        alpha_type = str(raw.get("type") or raw.get("alphaType") or "REGULAR").upper()
        pipeline = DescriptionPipeline(self.database)
        schema = pipeline.schemas.observe_from_payload(
            alpha_type=alpha_type,
            source="platform_alpha_metadata",
            payload=raw,
            source_version=str(raw.get("version") or raw.get("updatedAt") or ""),
        )
        field_metadata = {
            str(name): dict(metadata)
            for name, metadata in (raw.get("fieldMetadata") or {}).items()
            if isinstance(metadata, dict)
        }
        for name in BASE_VARS:
            field_metadata.setdefault(name, {"description": "platform base field"})
        alpha = {
            "alpha_id": result.alpha_id,
            "alpha_type": alpha_type,
            "platform_status": raw.get("status") or "UNKNOWN",
            "submission_pending": bool(raw.get("submissionPending")),
            "uncertain_write": False,
            "checks_fresh": result.status.upper() == "COMPLETE",
            "checks": result.checks,
            "prod_corr_exception_confirmed": bool(raw.get("prodCorrExceptionConfirmed")),
            "description_required": bool(raw.get("descriptionRequired")),
            "description_valid": bool(raw.get("descriptionValid")),
            "schema_known": schema is not None,
        }
        prepared = pipeline.prepare(
            sync_id=self._ledger_sync_id(),
            alpha=alpha,
            expression=expression,
            field_metadata=field_metadata,
            operator_definitions=raw.get("operatorDefinitions") or {},
            hypothesis={
                "hypothesis_id": spec.hypothesis_id,
                "mechanism": spec.mechanism,
                "expected_direction": "higher signal values are long",
            },
            settings=settings,
        )
        return bool(prepared and prepared.validation.valid)

    def _claim(self, expression: str, settings: dict[str, Any]) -> bool:
        payload = {"type": "REGULAR", "regular": expression, "settings": settings}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            cursor = con.execute(
                """INSERT OR IGNORE INTO simulation_requests
                (request_hash,payload_json,status,created_at,updated_at)
                VALUES (?,?,'CLAIMED',?,?)""",
                (request_hash, encoded, now, now),
            )
        return cursor.rowcount == 1

    def _record(
        self,
        spec: ResearchSpec,
        expression: str,
        settings: dict[str, Any],
        result: SimulationResult,
        outcome: BaselineOutcome | None,
    ) -> None:
        expression_id = expression_id_for(expression)
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            con.execute(
                """INSERT OR IGNORE INTO expressions
                (expression_id,expression_text,normalized_text,structure_sig,hypothesis_id,
                 generation_strategy,generation_layer,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    expression_id,
                    expression,
                    "".join(expression.lower().split()),
                    operator_topology(expression),
                    None if spec.fallback else spec.hypothesis_id,
                    "consultant_generator",
                    "group_rank_disabled",
                    now,
                ),
            )
            con.execute(
                """INSERT INTO simulation_runs
                (utc_iso,alpha_id,expression,status,queue_status,sharpe,fitness,turnover,fail_reason)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    now,
                    result.alpha_id,
                    expression,
                    result.status,
                    outcome.value if outcome else "UNKNOWN",
                    result.metrics.get("sharpe"),
                    result.metrics.get("fitness"),
                    result.metrics.get("turnover"),
                    "" if outcome is BaselineOutcome.PASS else (outcome.value if outcome else result.status),
                ),
            )
        if "sharpe" in result.metrics:
            checks = {
                str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "UNKNOWN").upper()
                for item in result.checks
                if isinstance(item, dict)
            }
            ResearchArmTracker(self.database).record_window(
                ArmDimensions(
                    spec.family,
                    spec.dataset,
                    spec.family,
                    spec.mechanism,
                    operator_topology(expression),
                    str(settings.get("region") or "USA"),
                    str(settings.get("universe") or "TOP3000"),
                    str(settings.get("delay") if settings.get("delay") is not None else "1"),
                ),
                sharpes=[float(result.metrics["sharpe"])],
                base_passes=[outcome is BaselineOutcome.PASS],
                near_passes=[outcome is BaselineOutcome.NEAR_PASS],
                self_corr_passes=int(checks.get("SELF_CORRELATION") == "PASS"),
                prod_corr_passes=int(
                    checks.get("PROD_CORRELATION", checks.get("PRODUCTION_CORRELATION")) == "PASS"
                ),
                final_submits=0,
            )

    def run_simulate(self, *, batch_size: int) -> FactoryCycleSummary:
        generated = simulated = far_fail = near_pass = passed = failed = 0
        descriptions_validated = 0
        threshold = self._live_sharpe_threshold()
        candidate_specs = [
            (spec, candidate)
            for spec in self._research_specs()
            for candidate in self.generator.generate(
                hypothesis_id=spec.hypothesis_id,
                family=spec.family,
                fields=spec.fields,
            )
        ]
        claimed_behaviors: set[str] = set()
        for spec, candidate in candidate_specs:
            if simulated >= max(0, int(batch_size)):
                break
            behavior = behavior_signature(candidate.expression)
            if not behavior or behavior in claimed_behaviors:
                continue
            settings = SettingsOptimizer(max_local_trials=4).stage1_default(spec.family)
            if not self._claim(candidate.expression, settings):
                continue
            claimed_behaviors.add(behavior)
            generated += 1
            try:
                result = self.simulation.simulate(
                    expression=candidate.expression, settings=settings, alpha_type="REGULAR"
                )
                simulated += 1
                sharpe = result.metrics.get("sharpe")
                outcome = (
                    classify_baseline(sharpe=float(sharpe), live_threshold=threshold)
                    if sharpe is not None
                    else None
                )
                far_fail += int(outcome is BaselineOutcome.FAR_FAIL)
                near_pass += int(outcome is BaselineOutcome.NEAR_PASS)
                passed += int(outcome is BaselineOutcome.PASS)
                self._record(spec, candidate.expression, settings, result, outcome)
                descriptions_validated += int(
                    self._prepare_description(
                        spec=spec,
                        expression=candidate.expression,
                        settings=settings,
                        result=result,
                    )
                )
            except Exception:
                failed += 1
                with sqlite3.connect(self.database) as con:
                    con.execute(
                        "UPDATE simulation_requests SET status='FAILED',updated_at=? WHERE payload_json LIKE ?",
                        (_utc_now(), f'%"regular":"{candidate.expression}"%'),
                    )
        return FactoryCycleSummary(
            generated,
            simulated,
            far_fail,
            near_pass,
            passed,
            failed,
            descriptions_validated,
        )
