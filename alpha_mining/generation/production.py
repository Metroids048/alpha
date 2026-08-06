"""CLI and long-running loop for pure, local Alpha candidate production."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from alpha_mining.common import load_workspace_env
from alpha_mining.domain.expression_normalization import (
    behavior_signature,
    exact_hash,
    expression_identity,
    extract_fields,
    normalized_expression,
    operator_topology,
    structure_signature,
)
from alpha_mining.generation.high_quality import (
    HighQualityGenerator,
    LLMUnavailable,
    revalidate_pending_rows,
)
from alpha_mining.generation.portfolio import PortfolioLimits
from alpha_mining.generation.snapshots import (
    DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS,
    CatalogUnavailable,
    load_local_snapshots,
)
from alpha_mining.generation.v50_kernel import V50Kernel
from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository
from alpha_mining.llm.deepseek import DeepSeekLLMError, DeepSeekStructuredLLM
from alpha_mining.storage.csv_queue import CandidateCsvQueue
from alpha_mining.storage.work_items import (
    CandidateWorkStore,
    WorkflowStatus,
    initialize_authoritative_database,
)


LOG = logging.getLogger("alpha_mining.generation.production")
MAX_NO_EVIDENCE_SKIPS = 3


@dataclass(frozen=True)
class ProductionConfig:
    root: Path = Path(".")
    database: Path | None = None
    catalog_dir: Path | None = None
    candidates_per_cycle: int = 3
    interval_seconds: float = 300.0
    allow_degraded: bool = False
    knowledge_root: Path | None = None
    pending_limit: int = 20
    portfolio_mode: str = "enforce"
    portfolio_limits: PortfolioLimits = field(default_factory=PortfolioLimits)
    offline_catalog_max_age_hours: float = DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS

    @property
    def queue_path(self) -> Path:
        return self.root / "待提交Alpha列表.csv"

    @property
    def events_path(self) -> Path:
        return (
            self.root / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv"
        )

    @property
    def database_path(self) -> Path:
        return (
            self.database
            or self.root / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
        )

    @property
    def worldquant_root(self) -> Path:
        return self.knowledge_root or Path("World quant")


@dataclass(frozen=True)
class CycleSummary:
    cycle_id: str
    state: str
    catalog_fields: int = 0
    catalog_operators: int = 0
    catalog_datasets: int = 0
    feedback_records: int = 0
    positive_feedback: int = 0
    near_pass_feedback: int = 0
    self_corr_risk: int = 0
    knowledge_snippets: int = 0
    llm_model: str = ""
    v50_seeds: int = 0
    llm_candidates: int = 0
    enqueued: int = 0
    pending_total: int = 0
    rejections: dict[str, int] | None = None
    detail: str = ""
    queue_rows: tuple[dict[str, str], ...] = ()
    next_wait_seconds: float = 0.0


@dataclass
class GenerationLoopState:
    last_input_fingerprint: str = ""
    last_enqueued: int | None = None
    zero_output_streak: int = 0


def run_cycle(
    config: ProductionConfig,
    *,
    llm: Any | None = None,
    kernel: Any | None = None,
    runtime_state: GenerationLoopState | None = None,
) -> CycleSummary:
    """Run one read-local, LLM-required generation cycle without platform I/O."""

    cycle_id = _cycle_id()
    if config.database is None:
        initialize_authoritative_database(
            config.database_path, config.root / "research_memory.sqlite"
        )
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    work_items = CandidateWorkStore(config.database_path)
    # Import the pre-fusion queue once.  From this point, SQLite owns state and
    # CSV is regenerated only after its transaction has committed.
    work_items.import_csv(config.queue_path)
    work_items.project_csv(config.queue_path, config.events_path)
    existing_rows = tuple(queue.read())
    try:
        snapshots = load_local_snapshots(
            root=config.root,
            catalog_dir=config.catalog_dir,
            database=config.database_path,
            queue_path=config.queue_path,
            allow_partial_offline=True,
            offline_max_age_hours=config.offline_catalog_max_age_hours,
        )
    except CatalogUnavailable as exc:
        _log_cycle(cycle_id, "CATALOG_UNAVAILABLE", detail=str(exc))
        return CycleSummary(
            cycle_id, "CATALOG_UNAVAILABLE", detail=str(exc), queue_rows=existing_rows
        )
    revalidated_rows, quarantined = revalidate_pending_rows(
        list(existing_rows), snapshots
    )
    if quarantined:
        revalidated_by_id = {str(row.get("candidate_id") or ""): row for row in revalidated_rows}
        for candidate_id, reason in quarantined:
            row = revalidated_by_id.get(candidate_id, {})
            work_items.transition(
                candidate_id,
                WorkflowStatus.REJECTED_LOCAL_REVALIDATION.value,
                event_type="LOCAL_REVALIDATION_REJECTED",
                details={"reason": reason},
                error_category=str(row.get("last_error_category") or "LOCAL_REVALIDATION"),
                error=str(row.get("last_error") or reason),
            )
        work_items.project_csv(config.queue_path, config.events_path)
        existing_rows = tuple(queue.read())

    pending_before = sum(
        row.get("queue_status") == "PENDING_SIMULATION" for row in existing_rows
    )
    fingerprint = _input_fingerprint(snapshots, existing_rows, config.worldquant_root)
    if pending_before >= max(1, int(config.pending_limit)):
        summary = _summary_from_snapshot(
            cycle_id,
            "WAITING_FOR_CONSUMER",
            snapshots,
            existing_rows,
            detail=f"pending queue reached limit {config.pending_limit}",
            pending_total=pending_before,
            next_wait_seconds=config.interval_seconds,
        )
        _log_cycle(
            cycle_id,
            summary.state,
            pending=summary.pending_total,
            detail=summary.detail,
        )
        return summary
    forced_refresh = False
    if (
        runtime_state is not None
        and runtime_state.last_enqueued == 0
        and runtime_state.last_input_fingerprint == fingerprint
    ):
        # The first zero-output generation already records streak=1.  Allow
        # three additional quiet rounds before forcing a fresh LLM attempt.
        if runtime_state.zero_output_streak <= MAX_NO_EVIDENCE_SKIPS:
            runtime_state.zero_output_streak += 1
            wait_seconds = _backoff_seconds(
                config.interval_seconds, runtime_state.zero_output_streak
            )
            summary = _summary_from_snapshot(
                cycle_id,
                "NO_NEW_EVIDENCE",
                snapshots,
                existing_rows,
                detail="catalog, grounded feedback and candidate inventory are unchanged",
                pending_total=pending_before,
                next_wait_seconds=wait_seconds,
            )
            _log_cycle(
                cycle_id,
                summary.state,
                pending=summary.pending_total,
                next_round_wait=wait_seconds,
            )
            return summary
        # Force a fresh LLM attempt after a finite quiet period.  Keep the
        # streak until the attempt succeeds so transport/local failures are
        # retried immediately instead of being mistaken for empty output.
        forced_refresh = True

    owned_llm = False
    if llm is None:
        load_workspace_env(config.root / ".env")
        try:
            llm = DeepSeekStructuredLLM()
            owned_llm = True
        except (ValueError, DeepSeekLLMError) as exc:
            _event(queue, cycle_id, "LLM_UNAVAILABLE", type(exc).__name__)
            return _summary_from_snapshot(
                cycle_id,
                "LLM_UNAVAILABLE",
                snapshots,
                existing_rows,
                detail=type(exc).__name__,
            )
    try:
        generator = HighQualityGenerator(
            llm=llm,
            kernel=kernel or V50Kernel(),
            knowledge_repository=WorldQuantKnowledgeRepository(config.worldquant_root),
            portfolio_mode=config.portfolio_mode,
            portfolio_limits=config.portfolio_limits,
            portfolio_pending_limit=config.pending_limit,
            allow_degraded=config.allow_degraded,
        )
        result = generator.generate(
            snapshots,
            cycle_id=cycle_id,
            candidates_per_cycle=config.candidates_per_cycle,
        )
    except (DeepSeekLLMError, LLMUnavailable) as exc:
        detail = str(exc) or type(exc).__name__
        _event(queue, cycle_id, "LLM_UNAVAILABLE", detail)
        return _summary_from_snapshot(
            cycle_id, "LLM_UNAVAILABLE", snapshots, existing_rows, detail=detail
        )
    except Exception as exc:
        _event(queue, cycle_id, "LOCAL_FAILURE", type(exc).__name__)
        return _summary_from_snapshot(
            cycle_id,
            "GENERATION_FAILED",
            snapshots,
            existing_rows,
            detail=type(exc).__name__,
        )
    finally:
        if owned_llm:
            llm.close()
    enqueued = 0
    for candidate in result.accepted:
        row = _queue_row(candidate, model_id=str(getattr(llm, "model_id", "")))
        if work_items.upsert_candidate(
            row,
            state=WorkflowStatus.PENDING_SIMULATION.value,
            source_evidence={"source": "generation", "quality_evidence": candidate.quality_evidence},
            event_type="GENERATED",
        ):
            enqueued += 1
    # CSV compatibility data is never the source of a successful generation.
    work_items.project_csv(config.queue_path, config.events_path)
    rows = tuple(queue.read())
    pending = sum(row.get("queue_status") == "PENDING_SIMULATION" for row in rows)
    wait_seconds = config.interval_seconds
    if runtime_state is not None:
        if runtime_state.last_input_fingerprint != fingerprint:
            runtime_state.zero_output_streak = 0
        runtime_state.last_input_fingerprint = fingerprint
        runtime_state.last_enqueued = enqueued
        if enqueued == 0:
            runtime_state.zero_output_streak = (
                1 if forced_refresh else max(1, runtime_state.zero_output_streak)
            )
        else:
            runtime_state.zero_output_streak = 0
    summary = CycleSummary(
        cycle_id,
        "COMPLETE",
        len(snapshots.catalog.fields),
        len(snapshots.catalog.operators),
        len(snapshots.catalog.datasets),
        len(snapshots.feedback.records),
        len(snapshots.feedback.positive),
        len(snapshots.feedback.near_pass),
        len(snapshots.feedback.self_corr_risk),
        len(result.knowledge.snippets),
        str(getattr(llm, "model_id", "")),
        len(result.seeds),
        result.llm_candidates,
        enqueued,
        pending,
        result.rejections,
        queue_rows=rows,
        next_wait_seconds=wait_seconds,
    )
    _log_cycle(
        cycle_id,
        summary.state,
        catalog_fields=summary.catalog_fields,
        catalog_operators=summary.catalog_operators,
        catalog_datasets=summary.catalog_datasets,
        feedback=summary.feedback_records,
        positive=summary.positive_feedback,
        near_pass=summary.near_pass_feedback,
        self_corr=summary.self_corr_risk,
        knowledge=summary.knowledge_snippets,
        llm=summary.llm_model,
        seeds=summary.v50_seeds,
        llm_candidates=summary.llm_candidates,
        rejected=sum((summary.rejections or {}).values()),
        top_rejections=_rejection_digest(summary.rejections),
        enqueued=summary.enqueued,
        pending=summary.pending_total,
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    _install_console_interrupt_handler()
    parser = argparse.ArgumentParser(
        description="纯本地 Alpha 生产器：只生成待平台 simulate 的候选"
    )
    parser.add_argument(
        "--once", action="store_true", help="执行一轮；LLM 或 catalog 不可用时返回非零"
    )
    parser.add_argument(
        "--max-rounds", type=int, default=0, help="执行指定轮数后退出；0 为无限"
    )
    parser.add_argument("--interval", type=float, default=300.0, help="每轮等待秒数")
    parser.add_argument(
        "--candidates-per-cycle", type=int, default=3, help="每轮最多入队 1-5 条"
    )
    parser.add_argument(
        "--catalog-dir", type=Path, default=None, help="完整本地 catalog 目录"
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="显式允许未来受控降级；默认绝不降级",
    )
    parser.add_argument(
        "--pending-limit",
        type=int,
        default=20,
        help="待 simulate 队列上限；达到后暂停调用 LLM",
    )
    parser.add_argument(
        "--portfolio-mode",
        choices=("shadow", "enforce"),
        default="enforce",
        help="组合多样性策略模式；默认 enforce，shadow 仅记录新策略决策",
    )
    parser.add_argument(
        "--offline-catalog-max-age-hours",
        type=float,
        default=DEFAULT_OFFLINE_CATALOG_MAX_AGE_HOURS,
        help="缺 operators 时字段/数据集离线快照允许的最长年龄（小时）",
    )
    args = parser.parse_args(argv)
    if args.candidates_per_cycle < 1 or args.candidates_per_cycle > 5:
        parser.error("--candidates-per-cycle 必须在 1 到 5 之间")
    config = ProductionConfig(
        root=Path("."),
        catalog_dir=args.catalog_dir,
        candidates_per_cycle=args.candidates_per_cycle,
        interval_seconds=max(0.0, args.interval),
        allow_degraded=bool(args.allow_degraded),
        pending_limit=max(1, int(args.pending_limit)),
        portfolio_mode=args.portfolio_mode,
        offline_catalog_max_age_hours=max(
            1.0, float(args.offline_catalog_max_age_hours)
        ),
    )
    max_rounds = 1 if args.once else max(0, int(args.max_rounds))
    rounds = 0
    final_state = "COMPLETE"
    runtime_state = GenerationLoopState()
    try:
        while max_rounds == 0 or rounds < max_rounds:
            summary = run_cycle(config, runtime_state=runtime_state)
            rounds += 1
            final_state = summary.state
            if max_rounds and rounds >= max_rounds:
                break
            wait_seconds = summary.next_wait_seconds or config.interval_seconds
            LOG.info(
                "cycle_id=%s next_round_wait=%.1fs", summary.cycle_id, wait_seconds
            )
            time.sleep(wait_seconds)
    except KeyboardInterrupt:
        LOG.info("generation loop interrupted by operator after %s cycle(s)", rounds)
        return 0
    if final_state == "CATALOG_UNAVAILABLE":
        return 8
    if final_state == "LLM_UNAVAILABLE":
        return 7
    if final_state == "GENERATION_FAILED":
        return 9
    return 0


def _summary_from_snapshot(
    cycle_id: str,
    state: str,
    snapshots: Any,
    queue_rows: tuple[dict[str, str], ...],
    *,
    detail: str = "",
    pending_total: int | None = None,
    next_wait_seconds: float = 0.0,
) -> CycleSummary:
    return CycleSummary(
        cycle_id,
        state,
        len(snapshots.catalog.fields),
        len(snapshots.catalog.operators),
        len(snapshots.catalog.datasets),
        len(snapshots.feedback.records),
        len(snapshots.feedback.positive),
        len(snapshots.feedback.near_pass),
        len(snapshots.feedback.self_corr_risk),
        pending_total=(
            sum(row.get("queue_status") == "PENDING_SIMULATION" for row in queue_rows)
            if pending_total is None
            else pending_total
        ),
        detail=detail,
        queue_rows=queue_rows,
        next_wait_seconds=next_wait_seconds,
    )


def _queue_row(candidate: Any, *, model_id: str) -> dict[str, str]:
    settings = candidate.settings
    identity = expression_identity(candidate.expression)
    payload = json.dumps(
        {"expression": candidate.expression, "settings": settings},
        ensure_ascii=False,
        sort_keys=True,
    )
    request_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    degraded = bool(getattr(candidate, "degraded", False))
    return {
        "candidate_id": hashlib.sha256(
            ("candidate:" + request_hash).encode("utf-8")
        ).hexdigest(),
        "request_hash": request_hash,
        "expression": candidate.expression,
        "alpha_type": settings["alpha_type"],
        "region": settings["region"],
        "universe": settings["universe"],
        "delay": str(settings["delay"]),
        "decay": str(settings["decay"]),
        "neutralization": settings["neutralization"],
        "truncation": str(settings["truncation"]),
        "language": settings["language"],
        "data_fields": json.dumps(
            extract_fields(candidate.expression), ensure_ascii=False
        ),
        "datasets": json.dumps(candidate.datasets, ensure_ascii=False),
        "operator_family": operator_topology(candidate.expression),
        "exact_hash": identity.exact_hash,
        "parameter_skeleton": identity.parameter_skeleton,
        "normalized_hash": hashlib.sha256(
            normalized_expression(candidate.expression).encode("utf-8")
        ).hexdigest(),
        "structure_signature": structure_signature(candidate.expression),
        "behavior_signature": behavior_signature(candidate.expression),
        "canonical_signature": structure_signature(candidate.expression),
        "generator_source": candidate.generator_source,
        "parent_template": candidate.parent_seed,
        "parent_seed": candidate.parent_seed,
        "research_direction": candidate.research_direction,
        "economic_hypothesis": candidate.hypothesis,
        "economic_rationale": candidate.economic_rationale,
        "knowledge_refs_json": json.dumps(candidate.knowledge_refs, ensure_ascii=False),
        "context_refs_json": json.dumps(getattr(candidate, "context_refs", ()), ensure_ascii=False),
        "knowledge_context_hash": str(getattr(candidate, "knowledge_context_hash", "") or ""),
        "feedback_refs_json": json.dumps(candidate.feedback_refs, ensure_ascii=False),
        "anti_corr_design": candidate.anti_corr_design,
        "expected_turnover_behavior": candidate.expected_turnover_behavior,
        "local_quality_score": str(candidate.local_quality_score),
        "novelty_score": str(candidate.novelty_score),
        "self_corr_risk_score": str(candidate.self_corr_risk_score),
        "quality_evidence_json": json.dumps(
            candidate.quality_evidence, ensure_ascii=False, sort_keys=True
        ),
        "llm_model": model_id,
        "knowledge_usage_mode": (
            "DEGRADED_DETERMINISTIC_FALLBACK"
            if degraded
            else "LIVE_LLM_KNOWLEDGE"
        ),
        "degraded": str(degraded).lower(),
        "queue_status": "PENDING_SIMULATION",
        "alpha_id": "",
        "retry_count": "0",
        "last_error_category": "",
        "last_error": "",
        "field_skeleton": identity.field_skeleton,
    }


def _event(
    queue: CandidateCsvQueue, candidate_id: str, event_type: str, detail: str
) -> None:
    with queue.writer():
        queue.record_event(candidate_id, event_type, detail)


def _input_fingerprint(
    snapshots: Any,
    rows: tuple[dict[str, str], ...],
    knowledge_root: Path,
) -> str:
    catalog_fields = [
        {
            "id": field.field_id,
            "dataset": field.dataset_id,
            "coverage": field.coverage,
            "date_coverage": field.date_coverage,
            "user_count": field.user_count,
            "alpha_count": field.alpha_count,
        }
        for field in sorted(
            snapshots.catalog.fields.values(), key=lambda item: item.field_id
        )
    ]
    feedback = [
        {
            "ref_id": item.ref_id,
            "request_hash": item.request_hash,
            "outcome": item.outcome,
            "failure_types": item.failure_types,
            "expression": item.expression if item.grounded else "",
        }
        for item in snapshots.feedback.records
    ]
    inventory = [
        {
            "request_hash": row.get("request_hash", ""),
            "queue_status": row.get("queue_status", ""),
            "research_direction": row.get("research_direction", ""),
            "data_fields": row.get("data_fields", ""),
            "operator_family": row.get("operator_family", ""),
            "structure_signature": row.get("structure_signature", ""),
            "last_error_category": row.get("last_error_category", ""),
        }
        for row in rows
    ]
    payload = {
        "fields": catalog_fields,
        "operators": sorted(snapshots.catalog.operators),
        "datasets": sorted(snapshots.catalog.datasets),
        "feedback": feedback,
        "inventory": inventory,
        "inventory_rejection_counts": snapshots.inventory.rejection_counts,
        "knowledge": _knowledge_fingerprint(knowledge_root),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _knowledge_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.is_dir():
        return "MISSING"
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"UNREADABLE")
        digest.update(b"\0")
    return digest.hexdigest()


def _backoff_seconds(base: float, zero_output_streak: int) -> float:
    safe_base = max(0.0, float(base))
    multipliers = (1, 3, 6, 12)
    index = min(max(0, int(zero_output_streak) - 1), len(multipliers) - 1)
    return min(3600.0, safe_base * multipliers[index])


def _cycle_id() -> str:
    return "cycle_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _install_console_interrupt_handler() -> None:
    """Translate Windows CTRL_BREAK_EVENT into the normal graceful exit path."""

    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _raise_keyboard_interrupt)


def _raise_keyboard_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def _log_cycle(cycle_id: str, state: str, **values: Any) -> None:
    details = " ".join(
        f"{key}={value}" for key, value in values.items() if value not in (None, "")
    )
    LOG.info("cycle_id=%s state=%s %s", cycle_id, state, details)


def _rejection_digest(rejections: dict[str, int] | None, *, limit: int = 5) -> str:
    if not rejections:
        return ""
    return ",".join(
        f"{reason}:{count}"
        for reason, count in sorted(
            rejections.items(), key=lambda item: (-item[1], item[0])
        )[:limit]
    )
