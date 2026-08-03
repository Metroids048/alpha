"""Single active Alpha generation runtime; generation never submits."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from alpha_mining.domain.expression_normalization import operator_topology
from alpha_mining.factory.control import FactoryControl
from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.factory.v50_adapter import (
    FactoryCandidateProposal,
    adapt_v50_candidate,
    generate_candidates,
)
from alpha_mining.generation.feedback import CandidateFeedbackStore
from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
from alpha_mining.offline.metadata import MetadataCache, MetadataCacheError, MetadataCacheMissing, MetadataCacheStale
from alpha_mining.platform.catalog import PlatformCatalogSynchronizer, ReadOnlyExpressionCatalog
from alpha_mining.quality.decision import QualityStatus, evaluate_quality
from alpha_mining.scheduler.arm_metrics import ArmDimensions, ResearchArmTracker
from alpha_mining.simulate.settings_optimizer import SettingsOptimizer
from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore


@dataclass(frozen=True)
class GenerationCycleConfig:
    database: Path
    output: Path
    cache_dir: Path
    auth_state_file: Path
    lock_path: Path
    max_initial_candidates: int = 3
    max_cycle_simulations: int = 12
    max_24h_simulations: int = 24
    max_ready_per_cycle: int = 1
    max_repair_parents: int = 2
    max_repairs_per_parent: int = 4


@dataclass(frozen=True)
class GenerationCycleSummary:
    generated: int
    screened_out: int
    simulated: int
    ready: int
    near_pass: int
    far_fail: int
    failed: int
    unknown: int
    repaired: int
    state: str
    deferred_reason: str = ""


CandidateSource = Callable[[], tuple[list[Any], Any]]


def recovery_exit_code(exc: BaseException) -> int:
    from alpha_mining.auth.session_manager import AuthDailyLimitExceeded, AuthenticationFailed, AuthStateError
    from alpha_mining.platform.access import CircuitOpen

    if isinstance(exc, CircuitOpen):
        return 5
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
        return 6
    if isinstance(exc, (AuthenticationFailed, AuthDailyLimitExceeded, AuthStateError)):
        return 4
    message = str(exc).lower()
    if isinstance(exc, PermissionError) and any(token in message for token in ("authentication", "http 401", "session expired")):
        return 4
    try:
        import requests
        if isinstance(exc, requests.RequestException):
            return 3
    except ImportError:
        pass
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return 3
    return 7


def cycle_exit_code(summary: Any) -> int:
    """Map a completed generation/simulation cycle to its process exit code.

    Kept as a small pure adapter for operators and offline callers; it does not
    alter cycle state or bypass any submission guard.
    """
    if getattr(summary, "generation_state", "") == "CANDIDATE_SPACE_EXHAUSTED":
        return 9
    if getattr(summary, "state", "") in {"CATALOG_UNAVAILABLE", "BLOCKED", "FAILED"}:
        return 8
    if int(getattr(summary, "failed", 0) or 0) > 0:
        return 8
    if int(getattr(summary, "unknown", 0) or 0) > 0:
        return 8
    if int(getattr(summary, "simulated", 0) or 0) > 0:
        return 0
    if str(getattr(summary, "deferred_reason", "") or "").strip():
        return 8
    if int(getattr(summary, "generated", 0) or 0) == 0 and int(
        getattr(summary, "simulated", 0) or 0
    ) == 0:
        return 1
    return 0


def _sanitize_diagnostic(text: str) -> str:
    return re.sub(
        r"(?i)\b(password|passwd|token|cookie|authorization)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )


def _sanitized_traceback() -> str:
    return _sanitize_diagnostic(traceback.format_exc())


def _default_candidate_source(config: GenerationCycleConfig) -> tuple[list[Any], Any]:
    """Use only the preserved v50 candidate boundary."""

    del config
    return generate_candidates()


def _load_catalog(
    config: GenerationCycleConfig,
    *,
    catalog_client: Any | None,
) -> MetadataCache:
    try:
        return MetadataCache.from_platform_disk_cache(config.cache_dir, max_age_hours=24)
    except (MetadataCacheMissing, MetadataCacheStale, MetadataCacheError) as first_error:
        if catalog_client is None:
            from alpha_mining.platform.client import ReadOnlyPlatformClient

            catalog_client = ReadOnlyPlatformClient(
                state_path=config.auth_state_file,
                database=config.database,
                lock_path=config.lock_path,
                min_interval=2.0,
            )
        try:
            PlatformCatalogSynchronizer(config.cache_dir).sync(
                catalog_client, region="USA", universe="TOP3000", delay=1
            )
        except Exception as sync_error:
            raise MetadataCacheError(
                f"catalog sync attempted after {type(first_error).__name__}; "
                f"{type(sync_error).__name__}: {_sanitize_diagnostic(str(sync_error))[:320]}"
            ) from sync_error
        return MetadataCache.from_platform_disk_cache(config.cache_dir, max_age_hours=24)


def _arm_for(proposal: FactoryCandidateProposal) -> ArmDimensions:
    return ArmDimensions(
        proposal.research_family,
        proposal.dataset,
        proposal.field_family,
        proposal.mechanism,
        operator_topology(proposal.expression),
        "USA",
        "TOP3000",
        "1",
    )


def _terminal_simulations_last_24h(database: Path) -> int:
    cutoff = time.time() - 24 * 60 * 60
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
    try:
        with sqlite3.connect(database) as con:
            row = con.execute(
                """SELECT COUNT(*) FROM simulation_requests
                   WHERE status IN ('COMPLETE','FAILED','UNKNOWN') AND updated_at>=?""",
                (cutoff_iso,),
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0


def _record_feedback(
    feedback: CandidateFeedbackStore,
    tracker: ResearchArmTracker,
    proposal: FactoryCandidateProposal,
    request_hash: str,
    *,
    outcome: str,
    result: Any | None,
    quality_reasons: tuple[str, ...] = (),
    error_category: str = "",
    error_message: str = "",
) -> None:
    metrics = getattr(result, "metrics", {}) or {}
    checks = getattr(result, "checks", []) or []
    statuses = {
        str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "").upper()
        for item in checks if isinstance(item, dict)
    }
    feedback.record(
        request_hash or proposal.exact_hash,
        outcome,
        candidate_id=proposal.candidate_id,
        topic_id=proposal.topic_id,
        hypothesis_id=proposal.hypothesis_id,
        research_family=proposal.research_family,
        strategy_family=proposal.strategy_family,
        mechanism=proposal.mechanism,
        dataset=proposal.dataset,
        parent_template=proposal.parent_template,
        exact_hash=proposal.exact_hash,
        parameter_skeleton=proposal.parameter_skeleton,
        field_skeleton=proposal.field_skeleton,
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
        operator_topology=operator_topology(proposal.expression),
        region="USA",
        universe_name="TOP3000",
        delay="1",
    )
    tracker.record_observation(
        _arm_for(proposal),
        base_pass=outcome == QualityStatus.READY_TO_SUBMIT.value,
        sharpe=metrics.get("sharpe"),
        fitness=metrics.get("fitness"),
        near_pass=outcome == QualityStatus.NEAR_PASS.value,
        self_corr_pass=statuses.get("SELF_CORRELATION") == "PASS",
        prod_corr_pass=statuses.get("PROD_CORRELATION", statuses.get("PRODUCTION_CORRELATION")) == "PASS",
        final_submit=False,
    )


def _ready_row(proposal: FactoryCandidateProposal, request_hash: str, result: Any, reasons: tuple[str, ...], settings: dict[str, Any]) -> dict[str, Any]:
    checks = getattr(result, "checks", []) or []
    statuses = {
        str(item.get("name") or "").upper(): str(item.get("result") or item.get("status") or "").upper()
        for item in checks if isinstance(item, dict)
    }
    return {
        "alpha_id": result.alpha_id,
        "candidate_id": proposal.candidate_id,
        "exact_hash": proposal.exact_hash,
        "expression": proposal.expression,
        "research_family": proposal.research_family,
        "strategy_family": proposal.strategy_family,
        "source": proposal.mechanism,
        "dataset": proposal.dataset,
        "generator_source": proposal.generator_source,
        "settings_json": settings,
        "sharpe": result.metrics.get("sharpe"),
        "fitness": result.metrics.get("fitness"),
        "turnover": result.metrics.get("turnover"),
        "self_correlation": statuses.get("SELF_CORRELATION", ""),
        "prod_correlation": statuses.get("PROD_CORRELATION", statuses.get("PRODUCTION_CORRELATION", "")),
        "checks_json": checks,
        "quality_status": QualityStatus.READY_TO_SUBMIT.value,
        "quality_reasons_json": list(reasons),
        "request_hash": request_hash,
        "simulated_at": str((getattr(result, "raw", {}) or {}).get("simulatedAt") or ""),
    }


def run_generation_cycle(
    config: GenerationCycleConfig,
    *,
    candidate_source: CandidateSource | None = None,
    catalog_client: Any | None = None,
    gateway: Any | None = None,
) -> GenerationCycleSummary:
    control = FactoryControl(config.database)
    state = control.status()
    if state.hard_stop and state.stop_kind in {"manual", "security", "data_integrity"}:
        return GenerationCycleSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, "BLOCKED", state.reason)
    try:
        metadata = _load_catalog(config, catalog_client=catalog_client)
    except MetadataCacheError as exc:
        return GenerationCycleSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, "CATALOG_UNAVAILABLE", str(exc))

    try:
        candidates, v50_catalog = (candidate_source or (lambda: _default_candidate_source(config)))()
    except Exception as exc:
        return GenerationCycleSummary(0, 0, 0, 0, 0, 0, 1, 0, 0, "PARTIAL", _sanitize_diagnostic(f"v50 generation: {type(exc).__name__}: {exc}"))

    if gateway is None:
        from alpha_mining.platform.gateway import PlatformGateway
        gateway = PlatformGateway(
            state_path=config.auth_state_file,
            database=config.database,
            lock_path=config.lock_path,
            min_interval=2.0,
        )
    executor = FactoryOrchestrator(config.database, gateway)
    feedback = CandidateFeedbackStore(config.database)
    tracker = ResearchArmTracker(config.database)
    ready_store = ReadyAlphaCsvStore(config.output)
    catalog = ReadOnlyExpressionCatalog(metadata, max_age_hours=24)
    policy = CandidateScreeningPolicy(catalog=catalog, region="USA", universe="TOP3000", delay=1)
    optimizer = SettingsOptimizer(max_local_trials=config.max_repairs_per_parent, total_budget=config.max_cycle_simulations, per_candidate_budget=config.max_repairs_per_parent)
    counts = {"generated": 0, "screened_out": 0, "simulated": 0, "ready": 0, "near_pass": 0, "far_fail": 0, "failed": 0, "unknown": 0, "repaired": 0}
    seen_hashes: set[str] = set()
    seen_skeletons: set[str] = set()
    per_family: dict[str, int] = {}
    repair_parents = 0
    partial = False

    for candidate in sorted(candidates, key=lambda item: float(getattr(item, "score", 0.0) or 0.0), reverse=True):
        if counts["generated"] >= max(0, config.max_initial_candidates) or counts["simulated"] >= max(0, config.max_cycle_simulations) or counts["ready"] >= max(0, config.max_ready_per_cycle):
            break
        if _terminal_simulations_last_24h(config.database) >= max(0, config.max_24h_simulations):
            break
        try:
            proposal = adapt_v50_candidate(candidate, v50_catalog)
        except ValueError:
            counts["screened_out"] += 1
            continue
        arm_stats = tracker.stats(_arm_for(proposal))
        if arm_stats.sampling_weight <= 0 or (arm_stats.sampling_weight < 1.0 and per_family.get(proposal.strategy_family, 0) >= 1):
            counts["screened_out"] += 1
            continue
        screening = policy.screen_expression(
            proposal.expression,
            round_seen_hashes=seen_hashes,
            round_seen_skeletons=seen_skeletons,
            expected_dataset_id=proposal.dataset,
        )
        if screening not in {None, RejectionReason.NONE}:
            counts["screened_out"] += 1
            continue
        seen_hashes.add(proposal.exact_hash)
        seen_skeletons.add(proposal.field_skeleton)
        per_family[proposal.strategy_family] = per_family.get(proposal.strategy_family, 0) + 1
        counts["generated"] += 1
        settings = optimizer.stage1_default(proposal.strategy_family)
        execution = executor.execute_candidate(proposal, settings)
        if execution.result is None:
            outcome = "UNKNOWN" if execution.error_category == "LEASE_LOST" else "FAILED"
            try:
                _record_feedback(feedback, tracker, proposal, execution.request_hash, outcome=outcome, result=None, error_category=execution.error_category, error_message=execution.error_message)
            except Exception:
                partial = True
            counts["unknown" if outcome == "UNKNOWN" else "failed"] += 1
            continue
        counts["simulated"] += 1
        decision = evaluate_quality(alpha_id=execution.result.alpha_id, status=execution.result.status, metrics=execution.result.metrics, checks=execution.result.checks, prod_corr_exception_confirmed=bool((execution.result.raw or {}).get("prodCorrExceptionConfirmed")))
        try:
            _record_feedback(feedback, tracker, proposal, execution.request_hash, outcome=decision.status.value, result=execution.result, quality_reasons=decision.reasons)
        except Exception:
            partial = True
            continue
        if decision.status is QualityStatus.READY_TO_SUBMIT:
            counts["ready"] += int(ready_store.upsert(_ready_row(proposal, execution.request_hash, execution.result, decision.reasons, settings)))
            continue
        if decision.status is QualityStatus.NEAR_PASS:
            counts["near_pass"] += 1
            if arm_stats.sampling_weight <= 0.1 or repair_parents >= config.max_repair_parents:
                continue
            repair_parents += 1
            trials = optimizer.local_trials(settings, quality_score=0.8, metric_ratio=0.95, candidate_id=proposal.candidate_id, candidate_classification="NEAR_PASS")
            for trial in trials[: config.max_repairs_per_parent]:
                if counts["simulated"] >= config.max_cycle_simulations or _terminal_simulations_last_24h(config.database) >= config.max_24h_simulations:
                    break
                retry = executor.execute_candidate(proposal, trial.settings)
                counts["repaired"] += 1
                if retry.result is None:
                    counts["failed"] += 1
                    _record_feedback(feedback, tracker, proposal, retry.request_hash, outcome="FAILED", result=None, error_category=retry.error_category, error_message=retry.error_message)
                    continue
                counts["simulated"] += 1
                retry_decision = evaluate_quality(alpha_id=retry.result.alpha_id, status=retry.result.status, metrics=retry.result.metrics, checks=retry.result.checks, prod_corr_exception_confirmed=bool((retry.result.raw or {}).get("prodCorrExceptionConfirmed")))
                _record_feedback(feedback, tracker, proposal, retry.request_hash, outcome=retry_decision.status.value, result=retry.result, quality_reasons=retry_decision.reasons)
                if retry_decision.status is QualityStatus.READY_TO_SUBMIT:
                    counts["ready"] += int(ready_store.upsert(_ready_row(proposal, retry.request_hash, retry.result, retry_decision.reasons, trial.settings)))
                    break
            continue
        counts["far_fail"] += 1

    final_state = "PARTIAL" if partial else ("READY" if counts["ready"] else "COMPLETE")
    return GenerationCycleSummary(**counts, state=final_state)


def run_generation_loop(config: GenerationCycleConfig, *, max_rounds: int, interval_seconds: float) -> int:
    rounds = 0
    try:
        while max_rounds <= 0 or rounds < max_rounds:
            summary = run_generation_cycle(config)
            print(json.dumps(summary.__dict__, sort_keys=True, ensure_ascii=False))
            rounds += 1
            if summary.state in {"BLOCKED", "CATALOG_UNAVAILABLE"}:
                return 2 if summary.state == "BLOCKED" else 8
            if max_rounds > 0 and rounds >= max_rounds:
                return 0
            time.sleep(max(1.0, float(interval_seconds)))
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alpha_mining.factory.runtime")
    parser.add_argument("--database", default="数据/本地运行产物/数据库/research_memory.sqlite")
    parser.add_argument("--output", default="待提交Alpha列表.csv")
    parser.add_argument("--cache-dir", default=".")
    parser.add_argument("--auth-state-file", default=".wq_auth_state.json")
    parser.add_argument("--lock-path", default="worldquant_api.lock")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--max-initial-candidates", type=int, default=3)
    parser.add_argument("--max-cycle-simulations", type=int, default=12)
    parser.add_argument("--max-24h-simulations", type=int, default=24)
    parser.add_argument("--max-ready-per-cycle", type=int, default=1)
    parser.add_argument("--max-repair-parents", type=int, default=2)
    parser.add_argument("--max-repairs-per-parent", type=int, default=4)
    args = parser.parse_args(argv)
    config = GenerationCycleConfig(
        database=Path(args.database), output=Path(args.output), cache_dir=Path(args.cache_dir),
        auth_state_file=Path(args.auth_state_file), lock_path=Path(args.lock_path),
        max_initial_candidates=max(0, min(3, args.max_initial_candidates)),
        max_cycle_simulations=max(0, min(12, args.max_cycle_simulations)),
        max_24h_simulations=max(0, min(24, args.max_24h_simulations)),
        max_ready_per_cycle=max(0, min(1, args.max_ready_per_cycle)),
        max_repair_parents=max(0, min(2, args.max_repair_parents)),
        max_repairs_per_parent=max(0, min(4, args.max_repairs_per_parent)),
    )
    if args.once:
        summary = run_generation_cycle(config)
        print(json.dumps(summary.__dict__, sort_keys=True, ensure_ascii=False))
        return 8 if summary.state == "CATALOG_UNAVAILABLE" else (2 if summary.state == "BLOCKED" else 0)
    return run_generation_loop(config, max_rounds=args.max_rounds, interval_seconds=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
