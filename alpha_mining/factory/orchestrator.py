"""Authoritative sequential baseline-first generation and simulation cycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from alpha_mining.domain.expression_normalization import expression_identity, operator_topology
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
    deferred_reason: str = ""


class FactoryOrchestrator:
    def __init__(self, database: str | Path, simulation: SimulationService) -> None:
        self.database = Path(database)
        SqliteRunLog(self.database).initialize_schema()
        migrate(self.database)
        self.simulation = simulation
        self.generator = ConsultantGenerator()
        self._generation_deferral_reason = ""

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
        if reason := self._catalog_unavailable_reason(rows):
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
        self._generation_deferral_reason = "no active research specifications are available"
        self._record_factory_event("CATALOG_UNAVAILABLE", self._generation_deferral_reason)
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
        identity = expression_identity(expression)
        if not identity.parameter_skeleton or not identity.field_skeleton:
            return False
        payload = {"type": "REGULAR", "regular": expression, "settings": settings}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _utc_now()
        with sqlite3.connect(self.database) as con:
            historical = con.execute(
                """SELECT 1 FROM expression_identities
                   WHERE exact_hash=? LIMIT 1""",
                (identity.exact_hash,),
            ).fetchone()
            if historical:
                return False
            # Clear stale FAILED entries so UNIQUE constraints don't block retries.
            con.execute(
                "DELETE FROM factory_candidate_claims WHERE exact_hash=? AND status='FAILED'",
                (identity.exact_hash,),
            )
            # simulation_requests: UNIQUE on request_hash — stale FAILEDs block
            # INSERT OR IGNORE, causing rowcount=0 and silent skip of all candidates.
            con.execute(
                "DELETE FROM simulation_requests WHERE request_hash=? AND status='FAILED'",
                (request_hash,),
            )
            claim = con.execute(
                """INSERT OR IGNORE INTO factory_candidate_claims
                (expression_text,exact_hash,parameter_skeleton,field_skeleton,request_hash,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'CLAIMED',?,?)""",
                (
                    expression,
                    identity.exact_hash,
                    identity.parameter_skeleton,
                    identity.field_skeleton,
                    request_hash,
                    now,
                    now,
                ),
            )
            if claim.rowcount != 1:
                return False
            request = con.execute(
                """INSERT OR IGNORE INTO simulation_requests
                (request_hash,payload_json,status,created_at,updated_at)
                VALUES (?,?,'CLAIMED',?,?)""",
                (request_hash, encoded, now, now),
            )
            if request.rowcount != 1:
                con.execute(
                    "DELETE FROM factory_candidate_claims WHERE request_hash=?",
                    (request_hash,),
                )
                return False
        return True

    def _set_claim_status(self, expression: str, status: str) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE factory_candidate_claims SET status=?,updated_at=? WHERE exact_hash=?",
                (status, _utc_now(), expression_identity(expression).exact_hash),
            )

    def _record(
        self,
        spec: ResearchSpec,
        expression: str,
        settings: dict[str, Any],
        result: SimulationResult,
        outcome: BaselineOutcome | None,
    ) -> None:
        expression_id = expression_id_for(expression)
        identity = expression_identity(expression)
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
                (utc_iso,expression_id,alpha_id,expression,status,queue_status,sharpe,fitness,turnover,fail_reason,region,universe,delay)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now,
                    expression_id,
                    result.alpha_id,
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

    def _pending_requests(self, limit: int) -> list[tuple[str, str, dict[str, Any]]]:
        """Read claimed-but-unrun simulation requests oldest-first."""
        if limit <= 0:
            return []
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT request_hash,payload_json FROM simulation_requests
                   WHERE status='PENDING' ORDER BY created_at LIMIT ?""",
                (int(limit),),
            ).fetchall()
        pending: list[tuple[str, str, dict[str, Any]]] = []
        for request_hash, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            expression = str(payload.get("regular") or "").strip()
            settings = payload.get("settings")
            if not expression or not isinstance(settings, dict):
                continue
            pending.append((str(request_hash), expression, settings))
        return pending

    def _set_request_status(self, request_hash: str, status: str) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE simulation_requests SET status=?,updated_at=? WHERE request_hash=?",
                (status, _utc_now(), request_hash),
            )

    def _drain_pending_requests(
        self, *, batch_size: int, threshold: float
    ) -> tuple[int, int, int, int, int]:
        """Simulate requests already claimed by an earlier cycle.

        A request row survives a crash or an authentication outage after its
        candidate was claimed, so the work is already paid for in dedup terms.
        Re-running it here keeps a restarted loop from stalling on an empty
        candidate batch while unrun requests accumulate.

        Returns (simulated, far_fail, near_pass, baseline_pass, failed).
        """

        pending = self._pending_requests(batch_size)
        if not pending:
            return (0, 0, 0, 0, 0)
        simulated = far_fail = near_pass = passed = failed = 0
        spec = ResearchSpec(
            hypothesis_id="",
            family="PENDING_BACKLOG",
            mechanism="restored pending simulation request",
            horizon="medium",
            fields=(),
            dataset="UNKNOWN",
            fallback=True,
        )
        print(f"[factory] draining {len(pending)} pending simulation_requests")
        for request_hash, expression, settings in pending:
            try:
                result = self.simulation.simulate(
                    expression=expression, settings=settings, alpha_type="REGULAR"
                )
            except Exception as exc:
                failed += 1
                self._set_request_status(request_hash, "FAILED")
                self._set_claim_status(expression, "FAILED")
                print(f"[factory] {request_hash[:8]} FAILED: {type(exc).__name__}: {exc}")
                continue
            # A platform rejection is *returned*, not raised: the gateway yields a
            # result with an empty alpha id and a FAILED/ERROR/REJECTED status.
            # Counting that as simulated would mark the request COMPLETE and hide
            # a rejected expression behind a success.
            if not str(result.alpha_id or "").strip():
                failed += 1
                self._set_request_status(request_hash, "FAILED")
                self._set_claim_status(expression, "FAILED")
                print(f"[factory] {request_hash[:8]} REJECTED status={result.status}")
                continue
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
            self._record(spec, expression, settings, result, outcome)
            self._set_request_status(request_hash, "COMPLETE")
            self._set_claim_status(expression, "SIMULATED")
            print(
                f"[factory] {request_hash[:8]} {result.status} "
                f"sharpe={sharpe} alpha_id={result.alpha_id}"
            )
        return (simulated, far_fail, near_pass, passed, failed)

    def run_simulate(self, *, batch_size: int) -> FactoryCycleSummary:
        generated = simulated = far_fail = near_pass = passed = failed = 0
        descriptions_validated = 0
        self._generation_deferral_reason = ""
        threshold = self._live_sharpe_threshold()
        self._backfill_identities()
        drained = self._drain_pending_requests(batch_size=batch_size, threshold=threshold)
        simulated += drained[0]
        far_fail += drained[1]
        near_pass += drained[2]
        passed += drained[3]
        failed += drained[4]
        candidate_specs = [
            (spec, candidate)
            for spec in self._research_specs()
            for candidate in self.generator.generate(
                hypothesis_id=spec.hypothesis_id,
                family=spec.family,
                fields=spec.fields,
            )
        ]
        claimed_skeletons: set[str] = set()
        for spec, candidate in candidate_specs:
            if simulated >= max(0, int(batch_size)):
                break
            identity = expression_identity(candidate.expression)
            if not identity.field_skeleton or identity.field_skeleton in claimed_skeletons:
                continue
            settings = SettingsOptimizer(max_local_trials=4).stage1_default(spec.family)
            if not self._claim(candidate.expression, settings):
                continue
            claimed_skeletons.add(identity.field_skeleton)
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
                self._set_claim_status(candidate.expression, "SIMULATED")
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
                self._set_claim_status(candidate.expression, "FAILED")
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
            self._generation_deferral_reason,
        )
