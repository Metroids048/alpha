"""Operator-facing candidate workflow composed from the authoritative core.

This module owns orchestration only.  Platform traffic remains inside
``PlatformGateway`` and all request/checkpoint state remains in the existing
``SimulationRequestStore`` through ``FactoryOrchestrator``.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Protocol

from alpha_mining.factory.control import FactoryControl
from alpha_mining.factory.orchestrator import FactoryOrchestrator, ResearchSpec
from alpha_mining.quality.decision import QualityStatus, evaluate_quality
from alpha_mining.simulate.settings_optimizer import SettingsOptimizer, TuneStage
from alpha_mining.storage.work_items import CandidateWorkItem, CandidateWorkStore, WorkflowStatus


class WorkflowGateway(Protocol):
    def simulate(self, **kwargs: Any) -> Any: ...
    def refresh_alpha_checks(self, alpha_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WorkflowProgress:
    candidate_id: str
    state: str
    message: str
    simulated: int = 0


@dataclass(frozen=True)
class PreparationSummary:
    processed: int
    simulated: int
    states: dict[str, int]


@dataclass(frozen=True)
class BatchPreparation:
    batch_id: str
    payload_hash: str
    candidate_ids: tuple[str, ...]
    ready_for_confirmation: bool


class CandidateWorkflowService:
    """The only service used by the CLI and PyQt queue workbench."""

    def __init__(
        self,
        database: str | Path,
        gateway: WorkflowGateway | None = None,
        *,
        progress_sink: Callable[[WorkflowProgress], None] | None = None,
        max_simulations_per_round: int = 12,
        max_simulations_per_24h: int = 24,
    ) -> None:
        self.database = Path(database)
        self.store = CandidateWorkStore(self.database)
        if gateway is None:
            from alpha_mining.platform.gateway import PlatformGateway
            gateway = PlatformGateway(database=self.database)
        self.gateway = gateway
        self.orchestrator = FactoryOrchestrator(self.database, gateway)
        self.progress_sink = progress_sink or (lambda _event: None)
        self.max_simulations_per_round = max(1, int(max_simulations_per_round))
        self.max_simulations_per_24h = max(1, int(max_simulations_per_24h))

    def list_items(self, *, states: Iterable[str] | None = None, limit: int | None = None) -> list[CandidateWorkItem]:
        return self.store.list_items(states=states, limit=limit)

    def prepare_once(self, *, limit: int | None = None) -> PreparationSummary:
        cap = min(self.max_simulations_per_round, max(0, int(limit if limit is not None else self.max_simulations_per_round)))
        simulated = processed = 0
        states: dict[str, int] = {}
        # Check refresh never creates a simulation request or POSTs.
        for item in self.store.list_items(states=[WorkflowStatus.WAITING_CHECKS.value], limit=cap):
            processed += 1
            self._refresh_checks(item)
        remaining = max(0, cap - processed)
        if remaining and self._simulation_budget_available():
            for item in self.store.list_items(states=[WorkflowStatus.PENDING_SIMULATION.value], limit=remaining):
                processed += 1
                simulated += int(self._simulate(item))
        for item in self.store.list_items(limit=10000):
            states[item.state] = states.get(item.state, 0) + 1
        return PreparationSummary(processed, simulated, states)

    def run_forever(self, *, interval_seconds: float = 30.0, stop: Callable[[], bool] | None = None) -> None:
        should_stop = stop or (lambda: False)
        while not should_stop():
            self.prepare_once()
            time.sleep(max(0.1, float(interval_seconds)))

    def retry_item(self, candidate_id: str) -> CandidateWorkItem | None:
        item = self.store.get_item(candidate_id)
        if item is None:
            raise KeyError(candidate_id)
        if item.state == WorkflowStatus.WAITING_CHECKS.value:
            self._refresh_checks(item)
            return self.store.get_item(candidate_id)
        if item.state != WorkflowStatus.NEAR_PASS.value:
            return item
        settings = self._settings(item)
        options = (
            (TuneStage.STABILITY, 0),
            (TuneStage.DECAY_COARSE, 0),
            (TuneStage.DECAY_COARSE, 1),
            (TuneStage.DECAY_FINE, 0),
        )
        stage, trial_index = options[item.tune_child_count] if item.tune_child_count < len(options) else (TuneStage.DECAY_FINE, 0)
        trials = SettingsOptimizer.stage_trials(stage, settings)
        if not trials:
            return item
        trial = trials[min(trial_index, len(trials) - 1)]
        child = self.store.create_tune_child(item, trial.settings, stage.value)
        if child:
            self._emit(child, WorkflowStatus.PENDING_SIMULATION.value, f"tune child created: {stage.value}")
        return child

    def submit_batch(
        self,
        candidate_ids: Iterable[str],
        *,
        confirmation: str = "",
        execute: bool = False,
    ) -> BatchPreparation:
        items = self.store.list_items_for_ids(candidate_ids)
        if not items:
            raise ValueError("batch must contain candidates")
        allowed = {WorkflowStatus.DESCRIPTION_VALIDATED.value, WorkflowStatus.READY_TO_SUBMIT.value, WorkflowStatus.AWAITING_BATCH_CONFIRMATION.value}
        blocked = [item.candidate_id for item in items if item.state not in allowed or not item.alpha_id]
        if blocked:
            raise ValueError("batch contains candidates that are not ready: " + ",".join(blocked))
        batch_id, payload_hash = self.store.create_batch_intent(item.candidate_id for item in items)
        if not execute:
            for item in items:
                self.store.transition(item.candidate_id, WorkflowStatus.AWAITING_BATCH_CONFIRMATION.value, event_type="BATCH_PREPARED", details={"batch_id": batch_id})
            return BatchPreparation(batch_id, payload_hash, tuple(item.candidate_id for item in items), False)
        if confirmation != "I_UNDERSTAND_REAL_SUBMISSION":
            raise PermissionError("real submission confirmation is invalid")
        control = FactoryControl(self.database)
        if not (control.can_patch_description() and control.can_submit()):
            raise PermissionError("FactoryControl does not permit description and submission writes")
        payloads = {item.candidate_id: self._description_payload(item) for item in items}
        ids = self.store.confirm_batch(batch_id, payload_hash)
        for candidate_id in ids:
            self.store.transition(candidate_id, WorkflowStatus.SUBMITTING.value, event_type="BATCH_CONFIRMED", details={"batch_id": batch_id})
        from alpha_mining.description.delivery import DescriptionDelivery
        from alpha_mining.description.models import DescriptionStatus
        from alpha_mining.submitter.delivery import SubmissionDelivery, SubmissionStatus

        descriptions = DescriptionDelivery(self.database, self.gateway)
        submissions = SubmissionDelivery(self.database, self.gateway)
        for candidate_id in ids:
            item = self.store.get_item(candidate_id)
            assert item is not None
            sync_id, alpha_type, payload, payload_path = payloads[candidate_id]
            description = descriptions.patch_once(sync_id=sync_id, alpha_id=item.alpha_id, alpha_type=alpha_type, payload=payload, payload_path=payload_path, execute=True)
            if description.status is not DescriptionStatus.VERIFIED:
                state = WorkflowStatus.SUBMISSION_UNCERTAIN.value if description.uncertain else WorkflowStatus.DESCRIPTION_VALIDATED.value
                self.store.transition(candidate_id, state, event_type="DESCRIPTION_DELIVERY_INCOMPLETE", submission_status="NOT_SUBMITTED", error=description.error)
                continue
            submitted = submissions.submit_once(sync_id=sync_id, alpha_id=item.alpha_id, execute=True)
            if submitted.status is SubmissionStatus.VERIFIED:
                self.store.transition(candidate_id, WorkflowStatus.SUBMITTED.value, event_type="SUBMISSION_VERIFIED", description_status="VERIFIED", submission_status="VERIFIED")
            elif submitted.status is SubmissionStatus.UNCERTAIN:
                self.store.transition(candidate_id, WorkflowStatus.SUBMISSION_UNCERTAIN.value, event_type="SUBMISSION_UNCERTAIN", description_status="VERIFIED", submission_status="UNCERTAIN", error=submitted.error)
            else:
                self.store.transition(candidate_id, WorkflowStatus.DESCRIPTION_VALIDATED.value, event_type="SUBMISSION_FAILED", description_status="VERIFIED", submission_status="FAILED", error=submitted.error)
        return BatchPreparation(batch_id, payload_hash, ids, True)

    def _simulate(self, item: CandidateWorkItem) -> bool:
        self.store.transition(item.candidate_id, WorkflowStatus.SIMULATING.value, event_type="SIMULATION_STARTED")
        self._emit(item, WorkflowStatus.SIMULATING.value, "simulation request leased")
        proposal = self._proposal(item)
        execution = self.orchestrator.execute_candidate(proposal, self._settings(item))
        if execution.result is None:
            uncertain = execution.error_category == "SIMULATION_UNCERTAIN"
            state = WorkflowStatus.SIMULATION_UNCERTAIN.value if uncertain else WorkflowStatus.FAR_FAIL.value
            self.store.transition(item.candidate_id, state, event_type="SIMULATION_UNCERTAIN" if uncertain else "SIMULATION_FAILED", error_category=execution.error_category, error=execution.error_message)
            self._emit(item, state, execution.error_message)
            return False
        result = execution.result
        decision = evaluate_quality(alpha_id=result.alpha_id, status=result.status, metrics=result.metrics, checks=result.checks, prod_corr_exception_confirmed=bool((result.raw or {}).get("prodCorrExceptionConfirmed")))
        state = decision.status.value
        self.store.transition(item.candidate_id, state, event_type="QUALITY_EVALUATED", alpha_id=result.alpha_id, metrics=result.metrics, checks=result.checks, quality_reasons=decision.reasons)
        refreshed = self.store.get_item(item.candidate_id)
        if decision.status is QualityStatus.READY_TO_SUBMIT and refreshed is not None:
            self._prepare_description(refreshed, result.raw)
        self._emit(item, state, "; ".join(decision.reasons), simulated=1)
        return True

    def _refresh_checks(self, item: CandidateWorkItem) -> None:
        if not item.alpha_id:
            self.store.transition(item.candidate_id, WorkflowStatus.FAR_FAIL.value, event_type="CHECK_REFRESH_FAILED", error_category="ALPHA_ID_MISSING", error="waiting checks item has no alpha_id")
            return
        try:
            current = self.gateway.refresh_alpha_checks(item.alpha_id)
        except Exception as exc:
            self._emit(item, item.state, f"check refresh deferred: {type(exc).__name__}")
            return
        decision = evaluate_quality(alpha_id=item.alpha_id, status="COMPLETE", metrics=current.get("metrics") or {}, checks=current.get("checks") or {}, prod_corr_exception_confirmed=bool((current.get("raw") or {}).get("prodCorrExceptionConfirmed")))
        self.store.transition(item.candidate_id, decision.status.value, event_type="CHECKS_REFRESHED", metrics=current.get("metrics") or {}, checks=current.get("checks") or [], quality_reasons=decision.reasons)
        if decision.status is QualityStatus.READY_TO_SUBMIT:
            refreshed = self.store.get_item(item.candidate_id)
            if refreshed:
                self._prepare_description(refreshed, current.get("raw") or {})

    def _prepare_description(self, item: CandidateWorkItem, raw: dict[str, Any]) -> None:
        try:
            spec = ResearchSpec("", str(item.payload.get("operator_family") or "QUEUE"), str(item.payload.get("economic_hypothesis") or "candidate"), "medium", (), str(item.payload.get("datasets") or ""))
            result = SimpleNamespace(alpha_id=item.alpha_id, status="COMPLETE", checks=item.checks, raw=raw)
            valid = self.orchestrator._prepare_description(spec=spec, expression=str(item.payload.get("expression") or ""), settings=self._settings(item), result=result)
        except Exception as exc:
            self.store.transition(item.candidate_id, WorkflowStatus.READY_TO_SUBMIT.value, event_type="DESCRIPTION_DRAFT_DEFERRED", description_status="DRAFT_PENDING", error_category="DESCRIPTION_PREPARE", error=f"{type(exc).__name__}: {exc}")
            return
        if valid:
            self.store.transition(item.candidate_id, WorkflowStatus.DESCRIPTION_VALIDATED.value, event_type="DESCRIPTION_VALIDATED", description_status="VALIDATED")
        else:
            self.store.transition(item.candidate_id, WorkflowStatus.READY_TO_SUBMIT.value, event_type="DESCRIPTION_DRAFT_DEFERRED", description_status="DRAFT_PENDING")

    def _simulation_budget_available(self) -> bool:
        with sqlite3.connect(self.database) as con:
            count = int(con.execute("SELECT COUNT(*) FROM simulation_requests WHERE created_at >= datetime('now','-1 day')").fetchone()[0])
        return count < self.max_simulations_per_24h

    def _description_payload(self, item: CandidateWorkItem) -> tuple[str, str, dict[str, Any], tuple[str, ...]]:
        """Load an already validated draft before accepting real write intent."""
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT sync_id,alpha_type,description_payload_json,description_status
                   FROM description_backfill_jobs WHERE alpha_id=?
                   ORDER BY updated_at DESC LIMIT 1""",
                (item.alpha_id,),
            ).fetchone()
        if not row or str(row[3]) != "VALIDATED" or not str(row[2] or ""):
            raise ValueError(f"candidate {item.candidate_id} has no validated description payload")
        from alpha_mining.description.schema import DescriptionSchemaRegistry
        schema = DescriptionSchemaRegistry(self.database).resolve(str(row[1]))
        if schema is None:
            raise ValueError(f"candidate {item.candidate_id} has no description schema")
        import json
        return str(row[0]), str(row[1]), dict(json.loads(str(row[2]))), schema.payload_path

    @staticmethod
    def _settings(item: CandidateWorkItem) -> dict[str, Any]:
        payload = item.payload
        if isinstance(payload.get("settings"), dict):
            return dict(payload["settings"])
        return {
            "instrumentType": "EQUITY", "region": payload.get("region") or "USA", "universe": payload.get("universe") or "TOP3000",
            "delay": int(payload.get("delay") or 1), "decay": int(payload.get("decay") or 0),
            "neutralization": payload.get("neutralization") or "SUBINDUSTRY", "truncation": float(payload.get("truncation") or 0.08),
            "pasteurization": "ON", "unitHandling": "VERIFY", "nanHandling": "ON", "language": payload.get("language") or "FASTEXPR", "visualization": False,
        }

    @staticmethod
    def _proposal(item: CandidateWorkItem) -> SimpleNamespace:
        payload = item.payload
        return SimpleNamespace(
            candidate_id=item.candidate_id, expression=str(payload.get("expression") or ""), topic_id="", hypothesis_id="",
            research_family=str(payload.get("operator_family") or "QUEUE"), strategy_family=str(payload.get("operator_family") or "QUEUE"),
            mechanism=str(payload.get("economic_hypothesis") or "queue"), dataset=str(payload.get("datasets") or "UNKNOWN"),
            exact_hash=str(payload.get("exact_hash") or ""), parameter_skeleton=str(payload.get("parameter_skeleton") or ""),
            field_skeleton=str(payload.get("field_skeleton") or ""), knowledge_usage_mode=str(payload.get("knowledge_usage_mode") or "NONE"),
            knowledge_refs=(), context_refs=(), knowledge_context_hash="", degraded=False, parent_candidate_id=item.parent_candidate_id,
        )

    def _emit(self, item: CandidateWorkItem, state: str, message: str, *, simulated: int = 0) -> None:
        self.progress_sink(WorkflowProgress(item.candidate_id, state, message, simulated))
