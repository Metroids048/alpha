"""Authoritative sequential baseline-first generation and simulation cycle."""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.domain.expression_normalization import expression_identity, operator_topology
from alpha_mining.domain.operator_registry import BASE_VARS
from alpha_mining.description.pipeline import DescriptionPipeline
from alpha_mining.factory.contracts import (
    SimulationCheckpoint,
    SimulationOutcomeUnknown,
    validate_simulation_result,
)
from alpha_mining.factory.simulation_requests import RequestLease, SimulationRequestStore
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


@dataclass(frozen=True)
class CandidateExecutionResult:
    request_hash: str
    result: SimulationResult | None = None
    error_category: str = ""
    error_message: str = ""


class SimulationService(Protocol):
    def simulate(
        self,
        *,
        expression: str,
        settings: dict[str, Any],
        alpha_type: str = "REGULAR",
        checkpoint: SimulationCheckpoint | None = None,
        checkpoint_sink: Any | None = None,
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
    unknown: int = 0
    descriptions_validated: int = 0
    deferred_reason: str = ""
    generation_state: str = "READY"


class FactoryOrchestrator:
    def __init__(
        self,
        database: str | Path,
        simulation: SimulationService,
        *,
        lease_timeout_seconds: float = 900.0,
    ) -> None:
        self.database = Path(database)
        SqliteRunLog(self.database).initialize_schema()
        migrate(self.database)
        self.simulation = simulation
        self.generator = ConsultantGenerator()
        self._generation_deferral_reason = ""
        self._generation_state = "READY"
        self.requests = SimulationRequestStore(
            self.database, lease_timeout_seconds=lease_timeout_seconds
        )

    def execute_candidate(
        self, proposal: Any, settings: dict[str, Any], *, allow_existing_identity: bool = False
    ):
        """Execute one already-screened proposal through the sole request lifecycle.

        The caller owns quality classification; this boundary owns claiming,
        checkpoint-capable simulation, and transactional terminal state only.
        """
        context = {
            "candidate_id": str(getattr(proposal, "candidate_id", "")),
            "topic_id": str(getattr(proposal, "topic_id", "")),
            "hypothesis_id": str(getattr(proposal, "hypothesis_id", "")),
            "research_family": str(getattr(proposal, "research_family", "")),
            "strategy_family": str(getattr(proposal, "strategy_family", "")),
            "mechanism": str(getattr(proposal, "mechanism", "")),
            "dataset": str(getattr(proposal, "dataset", "")),
            "exact_hash": str(getattr(proposal, "exact_hash", "")),
            "parameter_skeleton": str(getattr(proposal, "parameter_skeleton", "")),
            "field_skeleton": str(getattr(proposal, "field_skeleton", "")),
            "knowledge_usage_mode": str(getattr(proposal, "knowledge_usage_mode", "NONE")),
            "knowledge_refs": list(getattr(proposal, "knowledge_refs", ()) or ()),
            "context_refs": list(getattr(proposal, "context_refs", ()) or ()),
            "knowledge_context_hash": str(getattr(proposal, "knowledge_context_hash", "")),
            "degraded": bool(getattr(proposal, "degraded", False)),
            "tune_parent_candidate_id": str(getattr(proposal, "parent_candidate_id", "")),
        }
        claim = self.requests.claim(
            proposal.expression, settings, context=context,
            allow_existing_identity=allow_existing_identity,
        )
        if not claim.claimed:
            return CandidateExecutionResult(claim.request_hash, error_category="CLAIM_REJECTED", error_message=claim.reason)
        leases = self.requests.acquire(1, request_hash=claim.request_hash)
        if not leases:
            return CandidateExecutionResult(claim.request_hash, error_category="LEASE_UNAVAILABLE", error_message="claimed request was not acquired")
        lease = leases[0]
        spec = ResearchSpec(
            hypothesis_id=context["hypothesis_id"], family=context["research_family"], mechanism=context["mechanism"],
            horizon="medium", fields=(context["dataset"],), dataset=context["dataset"],
        )
        try:
            result = self._call_simulation(lease)
        except SimulationOutcomeUnknown as exc:
            detail = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            self.requests.finalize_failure(
                lease.request_hash,
                lease_started_at=lease.lease_started_at,
                status="UNKNOWN",
                error=detail,
            )
            return CandidateExecutionResult(
                lease.request_hash,
                error_category="SIMULATION_UNCERTAIN",
                error_message=detail,
            )
        except Exception as exc:
            detail = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            self.requests.finalize_failure(lease.request_hash, lease_started_at=lease.lease_started_at, error=detail)
            return CandidateExecutionResult(lease.request_hash, error_category="SIMULATION_FAILED", error_message=detail)
        validation = validate_simulation_result(result)
        if not validation.valid:
            detail = self._sanitize_error(validation.reason)
            self.requests.finalize_failure(lease.request_hash, lease_started_at=lease.lease_started_at, error=detail)
            return CandidateExecutionResult(lease.request_hash, error_category="INVALID_RESULT", error_message=detail)
        finalized = self.requests.finalize_success(
            lease.request_hash, alpha_id=result.alpha_id, lease_started_at=lease.lease_started_at,
            write_success=lambda con: self._write_success(
                con, spec=spec, expression=lease.expression, settings=lease.settings, result=result, outcome=None
            ),
        )
        if not finalized:
            return CandidateExecutionResult(lease.request_hash, error_category="LEASE_LOST", error_message="terminal transition lost")
        return CandidateExecutionResult(lease.request_hash, result=result)

    def _catalog_unavailable_reason(self, mappings: list[tuple[Any, ...]]) -> str | None:
        max_age_seconds = 24 * 60 * 60
        self._refresh_operator_cache_from_ledger(max_age_seconds=max_age_seconds)
        if not mappings:
            return "no verified data_mappings are available"
        payloads: dict[str, dict[str, Any]] = {}
        cache_paths = {
            "data-field cache": ".alpha_datafields_cache.json",
            "dataset cache": ".alpha_datasets_cache.json",
            "operator cache": ".alpha_operators_cache.json",
        }
        for label, filename in cache_paths.items():
            try:
                payload = json.loads(
                    self.database.with_name(filename).read_text(encoding="utf-8")
                )
                if not isinstance(payload, dict):
                    return f"{label} is invalid"
                cached_at = float(payload["cached_at"])
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                return f"{label} is missing or invalid"
            if datetime.now(timezone.utc).timestamp() - cached_at > max_age_seconds:
                return f"{label} is stale"
            payloads[label] = payload
        try:
            fields_payload = payloads["data-field cache"]
            datasets_payload = payloads["dataset cache"]
            operators_payload = payloads["operator cache"]
        except KeyError:
            return "catalog cache payload is incomplete"
        field_ids = {
            str(row.get("id") or "").strip()
            for row in fields_payload.get("rows") or []
            if isinstance(row, dict)
        }
        field_datasets = {
            (
                str(row.get("id") or "").strip(),
                str(row.get("_ds") or (row.get("dataset") or {}).get("id") or "").strip(),
            )
            for row in fields_payload.get("rows") or []
            if isinstance(row, dict)
        }
        dataset_ids = {
            str(dataset).strip()
            for dataset in datasets_payload.get("dataset_ids") or []
            if str(dataset).strip()
        }
        operators = {
            str(operator).strip()
            for operator in operators_payload.get("operators") or []
            if str(operator).strip()
        }
        required_operators = {
            "rank",
            "ts_rank",
            "ts_delta",
            "ts_zscore",
            "ts_std_dev",
            "ts_mean",
        }
        if not field_ids:
            return "data-field cache has no verified field IDs"
        if not dataset_ids:
            return "dataset cache has no verified dataset IDs"
        if missing := required_operators - operators:
            return "operator cache is missing required operators: " + ", ".join(sorted(missing))
        if any(str(row[4]) not in field_ids for row in mappings):
            return "a mapped field is absent from the verified data-field cache"
        if any(str(row[5]) not in dataset_ids for row in mappings):
            return "a mapped dataset is absent from the verified dataset cache"
        if any((str(row[4]), str(row[5])) not in field_datasets for row in mappings):
            return "a mapped field-dataset pair is absent from the verified data-field cache"
        return None

    def _refresh_operator_cache_from_ledger(self, *, max_age_seconds: int) -> None:
        path = self.database.with_name(".alpha_operators_cache.json")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(existing.get("cached_at") or 0.0)
            if datetime.now(timezone.utc).timestamp() - cached_at <= max_age_seconds:
                return
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        operators: set[str] = set()
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - max_age_seconds, timezone.utc
        ).isoformat().replace("+00:00", "Z")
        try:
            with sqlite3.connect(self.database) as con:
                rows = con.execute(
                    """SELECT raw_payload_json FROM platform_alpha_observations
                       WHERE synced_at>=? ORDER BY synced_at DESC""",
                    (cutoff,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return
        for (raw_payload,) in rows:
            try:
                payload = json.loads(str(raw_payload))
            except (TypeError, json.JSONDecodeError):
                continue
            definitions = payload.get("operatorDefinitions") if isinstance(payload, dict) else None
            if isinstance(definitions, dict):
                operators.update(str(name).strip() for name in definitions if str(name).strip())
        if not operators:
            return
        required_operators = {"rank", "ts_rank", "ts_delta", "ts_zscore", "ts_std_dev", "ts_mean"}
        if not required_operators <= operators:
            return
        try:
            path.write_text(
                json.dumps(
                    {
                        "cached_at": datetime.now(timezone.utc).timestamp(),
                        "operators": sorted(operators),
                        "source": "platform_alpha_observations",
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            return

    def _research_specs(self) -> list[ResearchSpec]:
        with sqlite3.connect(self.database) as con:
            active_count = int(
                con.execute(
                    """SELECT COUNT(*) FROM hypotheses h
                       JOIN research_topics t ON t.topic_id=h.topic_id
                       WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1"""
                ).fetchone()[0]
            )
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
        if active_count == 0:
            self._generation_state = "NO_RESEARCH_SPECS"
            self._generation_deferral_reason = "no active research specifications are available"
            self._record_factory_event(
                "NO_RESEARCH_SPECS", self._generation_deferral_reason
            )
            return []
        if reason := self._catalog_unavailable_reason(rows):
            self._generation_state = "CATALOG_UNAVAILABLE"
            self._generation_deferral_reason = reason
            self._record_factory_event(
                "CATALOG_UNAVAILABLE",
                reason,
            )
            return []
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
        self._generation_state = "NO_RESEARCH_SPECS"
        self._generation_deferral_reason = "no usable research specifications are available"
        self._record_factory_event("NO_RESEARCH_SPECS", self._generation_deferral_reason)
        return []

    def _record_factory_event(self, category: str, detail: str) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                "INSERT INTO factory_events(category,detail,observed_at) VALUES (?,?,?)",
                (category, detail, _utc_now()),
            )

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

    def _backfill_identities(self) -> None:
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT e.expression_id,e.expression_text FROM expressions e
                   LEFT JOIN expression_identities i ON i.expression_id=e.expression_id
                   WHERE i.expression_id IS NULL"""
            ).fetchall()
            for expression_id, expression in rows:
                identity = expression_identity(str(expression))
                con.execute(
                    """INSERT OR IGNORE INTO expression_identities
                    (expression_id,exact_hash,parameter_skeleton,field_skeleton,created_at)
                    VALUES (?,?,?,?,?)""",
                    (
                        expression_id,
                        identity.exact_hash,
                        identity.parameter_skeleton,
                        identity.field_skeleton,
                        _utc_now(),
                    ),
                )

    def _claim(self, expression: str, settings: dict[str, Any]) -> bool:
        """Compatibility wrapper; the store owns the transaction."""

        return self.requests.claim(expression, settings).claimed

    def _write_success(
        self,
        con: sqlite3.Connection,
        *,
        spec: ResearchSpec,
        expression: str,
        settings: dict[str, Any],
        result: SimulationResult,
        outcome: BaselineOutcome | None,
    ) -> None:
        expression_id = expression_id_for(expression)
        identity = expression_identity(expression)
        now = _utc_now()
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
            """INSERT OR IGNORE INTO expression_identities
            (expression_id,exact_hash,parameter_skeleton,field_skeleton,created_at)
            VALUES (?,?,?,?,?)""",
            (
                expression_id,
                identity.exact_hash,
                identity.parameter_skeleton,
                identity.field_skeleton,
                now,
            ),
        )
        con.execute(
            """INSERT INTO simulation_runs
            (utc_iso,expression_id,alpha_id,expression,status,queue_status,sharpe,fitness,turnover,
             fail_reason,region,universe,delay)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                expression_id,
                str(result.alpha_id).strip(),
                expression,
                result.status,
                outcome.value if outcome else "UNKNOWN",
                result.metrics.get("sharpe"),
                result.metrics.get("fitness"),
                result.metrics.get("turnover"),
                "" if outcome is BaselineOutcome.PASS else (outcome.value if outcome else result.status),
                settings.get("region", "USA"),
                settings.get("universe", "TOP3000"),
                settings.get("delay", 1),
            ),
        )

    def _record_arm(
        self,
        spec: ResearchSpec,
        expression: str,
        settings: dict[str, Any],
        result: SimulationResult,
        outcome: BaselineOutcome | None,
    ) -> None:
        if "sharpe" in result.metrics:
            checks = {
                str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "UNKNOWN").upper()
                for item in result.checks
                if isinstance(item, dict)
            }
            ResearchArmTracker(self.database).record_observation(
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
                sharpe=float(result.metrics["sharpe"]),
                fitness=result.metrics.get("fitness"),
                base_pass=outcome is BaselineOutcome.PASS,
                near_pass=outcome is BaselineOutcome.NEAR_PASS,
                self_corr_pass=checks.get("SELF_CORRELATION") == "PASS",
                prod_corr_pass=checks.get("PROD_CORRELATION", checks.get("PRODUCTION_CORRELATION")) == "PASS",
                final_submit=False,
            )

    @staticmethod
    def _sanitize_error(exc: BaseException | str) -> str:
        text = str(exc)
        return re.sub(
            r"(?i)\b(password|passwd|token|cookie|authorization)\s*[:=]\s*[^\s,;]+",
            lambda match: f"{match.group(1)}=[REDACTED]",
            text,
        )[:1000]

    def _call_simulation(self, lease: RequestLease) -> SimulationResult:
        simulate = self.simulation.simulate
        parameters = inspect.signature(simulate).parameters.values()
        supports_checkpoint = any(
            parameter.name == "checkpoint" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        kwargs: dict[str, Any] = {
            "expression": lease.expression,
            "settings": lease.settings,
            "alpha_type": "REGULAR",
        }
        if supports_checkpoint:
            kwargs.update(
                checkpoint=SimulationCheckpoint(
                    progress_location=lease.progress_location,
                    alpha_id=lease.alpha_id,
                ),
                checkpoint_sink=lambda checkpoint: self.requests.checkpoint(
                    lease.request_hash,
                    lease_started_at=lease.lease_started_at,
                    progress_location=checkpoint.progress_location,
                    alpha_id=checkpoint.alpha_id,
                ),
            )
        return simulate(**kwargs)

    def _execute_lease(
        self, spec: ResearchSpec, lease: RequestLease, threshold: float
    ) -> dict[str, int]:
        counters = {
            "simulated": 0,
            "far_fail": 0,
            "near_pass": 0,
            "baseline_pass": 0,
            "failed": 0,
            "unknown": 0,
            "descriptions_validated": 0,
        }
        try:
            result = self._call_simulation(lease)
        except SimulationOutcomeUnknown as exc:
            detail = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            self.requests.finalize_failure(
                lease.request_hash,
                lease_started_at=lease.lease_started_at,
                status="UNKNOWN",
                error=detail,
            )
            counters["unknown"] = 1
            print(f"[factory] {lease.request_hash[:8]} UNKNOWN: {detail}")
            return counters
        except Exception as exc:
            detail = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            finalized = self.requests.finalize_failure(
                lease.request_hash, lease_started_at=lease.lease_started_at, error=detail
            )
            counters["failed" if finalized else "unknown"] = 1
            terminal = "FAILED" if finalized else "UNKNOWN"
            print(f"[factory] {lease.request_hash[:8]} {terminal}: {detail}")
            return counters

        validation = validate_simulation_result(result)
        if not validation.valid:
            detail = self._sanitize_error(
                f"{validation.reason}; status={validation.normalized_status}"
            )
            finalized = self.requests.finalize_failure(
                lease.request_hash, lease_started_at=lease.lease_started_at, error=detail
            )
            counters["failed" if finalized else "unknown"] = 1
            terminal = "FAILED" if finalized else "UNKNOWN"
            print(f"[factory] {lease.request_hash[:8]} {terminal}: {detail}")
            return counters

        sharpe = result.metrics.get("sharpe")
        outcome = (
            classify_baseline(sharpe=float(sharpe), live_threshold=threshold)
            if sharpe is not None
            else None
        )
        finalized = self.requests.finalize_success(
            lease.request_hash,
            alpha_id=result.alpha_id,
            lease_started_at=lease.lease_started_at,
            write_success=lambda con: self._write_success(
                con,
                spec=spec,
                expression=lease.expression,
                settings=lease.settings,
                result=result,
                outcome=outcome,
            ),
        )
        if not finalized:
            counters["unknown"] = 1
            print(f"[factory] {lease.request_hash[:8]} UNKNOWN: execution lease was lost")
            return counters
        counters["simulated"] = 1
        counters["far_fail"] = int(outcome is BaselineOutcome.FAR_FAIL)
        counters["near_pass"] = int(outcome is BaselineOutcome.NEAR_PASS)
        counters["baseline_pass"] = int(outcome is BaselineOutcome.PASS)
        try:
            self._record_arm(spec, lease.expression, lease.settings, result, outcome)
        except Exception as exc:
            print(f"[factory] warning: arm metrics unavailable: {type(exc).__name__}")
        try:
            counters["descriptions_validated"] = int(
                self._prepare_description(
                    spec=spec,
                    expression=lease.expression,
                    settings=lease.settings,
                    result=result,
                )
            )
        except Exception as exc:
            print(f"[factory] warning: description preparation failed: {type(exc).__name__}")
        print(
            f"[factory] {lease.request_hash[:8]} {validation.normalized_status} "
            f"sharpe={sharpe} alpha_id={result.alpha_id}"
        )
        return counters

    def run_simulate(self, *, batch_size: int) -> FactoryCycleSummary:
        generated = simulated = far_fail = near_pass = passed = failed = unknown = 0
        descriptions_validated = 0
        self._generation_deferral_reason = ""
        self._generation_state = "READY"
        threshold = self._live_sharpe_threshold()
        self._backfill_identities()
        limit = max(0, int(batch_size))
        pending = self.requests.acquire(limit)
        attempted = len(pending)
        fallback_spec = ResearchSpec(
            hypothesis_id="",
            family="PENDING_BACKLOG",
            mechanism="restored pending simulation request",
            horizon="medium",
            fields=(),
            dataset="UNKNOWN",
            fallback=True,
        )
        if pending:
            print(f"[factory] draining {len(pending)} recoverable simulation_requests")
        for lease in pending:
            counts = self._execute_lease(fallback_spec, lease, threshold)
            simulated += counts["simulated"]
            far_fail += counts["far_fail"]
            near_pass += counts["near_pass"]
            passed += counts["baseline_pass"]
            failed += counts["failed"]
            unknown += counts["unknown"]
            descriptions_validated += counts["descriptions_validated"]

        # Candidate generation is owned by the active runtime; this legacy
        # orchestrator path only drains requests and can use its local fallback.
        candidate_specs = [
                (spec, candidate)
                for spec in self._research_specs()
                for candidate in self.generator.generate(
                    hypothesis_id=spec.hypothesis_id,
                    family=spec.family,
                    mechanism=spec.mechanism,
                    horizon=spec.horizon,
                    fields=spec.fields,
                )
        ]
        exact_duplicates = 0
        for spec, candidate in candidate_specs:
            if attempted >= limit:
                break
            settings = SettingsOptimizer(max_local_trials=4).stage1_default(spec.family)
            claim = self.requests.claim(candidate.expression, settings)
            if not claim.claimed:
                exact_duplicates += int(claim.reason == "exact_hash_exists")
                continue
            generated += 1
            leases = self.requests.acquire(1, request_hash=claim.request_hash)
            if not leases:
                continue
            attempted += 1
            counts = self._execute_lease(spec, leases[0], threshold)
            simulated += counts["simulated"]
            far_fail += counts["far_fail"]
            near_pass += counts["near_pass"]
            passed += counts["baseline_pass"]
            failed += counts["failed"]
            unknown += counts["unknown"]
            descriptions_validated += counts["descriptions_validated"]
        if (
                "candidate_specs" in dir()
                and candidate_specs
                and generated == 0
                and attempted == 0
                and exact_duplicates == len(candidate_specs)
        ):
            self._generation_state = "CANDIDATE_SPACE_EXHAUSTED"
            self._generation_deferral_reason = (
                "all generated candidate Exact Hash identities already exist"
            )
            self._record_factory_event(
                "CANDIDATE_SPACE_EXHAUSTED", self._generation_deferral_reason
            )
        return FactoryCycleSummary(
            generated=generated,
            simulated=simulated,
            far_fail=far_fail,
            near_pass=near_pass,
            baseline_pass=passed,
            failed=failed,
            unknown=unknown,
            descriptions_validated=descriptions_validated,
            deferred_reason=self._generation_deferral_reason,
            generation_state=self._generation_state,
        )
