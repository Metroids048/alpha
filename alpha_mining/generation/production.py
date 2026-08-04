"""CLI and long-running loop for pure, local Alpha candidate production."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from alpha_mining.common import load_workspace_env
from alpha_mining.generation.high_quality import HighQualityGenerator
from alpha_mining.generation.snapshots import CatalogUnavailable, load_local_snapshots
from alpha_mining.generation.v50_kernel import V50Kernel
from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository
from alpha_mining.llm.deepseek import DeepSeekLLMError, DeepSeekStructuredLLM
from alpha_mining.storage.csv_queue import CandidateCsvQueue


LOG = logging.getLogger("alpha_mining.generation.production")


@dataclass(frozen=True)
class ProductionConfig:
    root: Path = Path(".")
    database: Path | None = None
    catalog_dir: Path | None = None
    candidates_per_cycle: int = 3
    interval_seconds: float = 300.0
    allow_degraded: bool = False
    knowledge_root: Path | None = None

    @property
    def queue_path(self) -> Path:
        return self.root / "待提交Alpha列表.csv"

    @property
    def events_path(self) -> Path:
        return self.root / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv"

    @property
    def database_path(self) -> Path:
        return self.database or self.root / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"

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


def run_cycle(
    config: ProductionConfig,
    *,
    llm: Any | None = None,
    kernel: Any | None = None,
) -> CycleSummary:
    """Run one read-local, LLM-required generation cycle without platform I/O."""

    cycle_id = _cycle_id()
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    existing_rows = tuple(queue.read())
    try:
        snapshots = load_local_snapshots(
            root=config.root,
            catalog_dir=config.catalog_dir,
            database=config.database_path,
            queue_path=config.queue_path,
        )
    except CatalogUnavailable as exc:
        _log_cycle(cycle_id, "CATALOG_UNAVAILABLE", detail=str(exc))
        return CycleSummary(cycle_id, "CATALOG_UNAVAILABLE", detail=str(exc), queue_rows=existing_rows)
    owned_llm = False
    if llm is None:
        load_workspace_env(config.root / ".env")
        try:
            llm = DeepSeekStructuredLLM()
            owned_llm = True
        except (ValueError, DeepSeekLLMError) as exc:
            _event(queue, cycle_id, "LLM_UNAVAILABLE", type(exc).__name__)
            return _summary_from_snapshot(cycle_id, "LLM_UNAVAILABLE", snapshots, existing_rows, detail=type(exc).__name__)
    try:
        generator = HighQualityGenerator(
            llm=llm,
            kernel=kernel or V50Kernel(),
            knowledge_repository=WorldQuantKnowledgeRepository(config.worldquant_root),
        )
        result = generator.generate(snapshots, cycle_id=cycle_id, candidates_per_cycle=config.candidates_per_cycle)
    except Exception as exc:
        _event(queue, cycle_id, "LLM_UNAVAILABLE", type(exc).__name__)
        return _summary_from_snapshot(cycle_id, "LLM_UNAVAILABLE", snapshots, existing_rows, detail=type(exc).__name__)
    finally:
        if owned_llm:
            llm.close()
    enqueued = 0
    with queue.writer():
        for reason, count in result.rejections.items():
            if count:
                queue.record_event(cycle_id, "LOCAL_REJECTED", f"{reason}:{count}")
        for candidate in result.accepted:
            row = _queue_row(candidate, model_id=str(getattr(llm, "model_id", "")))
            queue.record_event(row["candidate_id"], "GENERATED", "LLM researched and locally validated")
            if queue.upsert(row):
                enqueued += 1
    rows = tuple(queue.read())
    pending = sum(row.get("queue_status") == "PENDING_SIMULATION" for row in rows)
    summary = CycleSummary(
        cycle_id, "COMPLETE", len(snapshots.catalog.fields), len(snapshots.catalog.operators), len(snapshots.catalog.datasets),
        len(snapshots.feedback.records), len(snapshots.feedback.positive), len(snapshots.feedback.near_pass),
        len(snapshots.feedback.self_corr_risk), len(result.knowledge.snippets), str(getattr(llm, "model_id", "")),
        len(result.seeds), result.llm_candidates, enqueued, pending, result.rejections, queue_rows=rows,
    )
    _log_cycle(
        cycle_id, summary.state, catalog_fields=summary.catalog_fields, catalog_operators=summary.catalog_operators,
        catalog_datasets=summary.catalog_datasets, feedback=summary.feedback_records, positive=summary.positive_feedback,
        near_pass=summary.near_pass_feedback, self_corr=summary.self_corr_risk, knowledge=summary.knowledge_snippets,
        llm=summary.llm_model, seeds=summary.v50_seeds, llm_candidates=summary.llm_candidates,
        rejected=sum((summary.rejections or {}).values()), enqueued=summary.enqueued, pending=summary.pending_total,
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="纯本地 Alpha 生产器：只生成待平台 simulate 的候选")
    parser.add_argument("--once", action="store_true", help="执行一轮；LLM 或 catalog 不可用时返回非零")
    parser.add_argument("--max-rounds", type=int, default=0, help="执行指定轮数后退出；0 为无限")
    parser.add_argument("--interval", type=float, default=300.0, help="每轮等待秒数")
    parser.add_argument("--candidates-per-cycle", type=int, default=3, help="每轮最多入队 1-5 条")
    parser.add_argument("--catalog-dir", type=Path, default=None, help="完整本地 catalog 目录")
    parser.add_argument("--allow-degraded", action="store_true", help="显式允许未来受控降级；默认绝不降级")
    args = parser.parse_args(argv)
    if args.candidates_per_cycle < 1 or args.candidates_per_cycle > 5:
        parser.error("--candidates-per-cycle 必须在 1 到 5 之间")
    config = ProductionConfig(
        root=Path("."), catalog_dir=args.catalog_dir, candidates_per_cycle=args.candidates_per_cycle,
        interval_seconds=max(0.0, args.interval), allow_degraded=bool(args.allow_degraded),
    )
    max_rounds = 1 if args.once else max(0, int(args.max_rounds))
    rounds = 0
    final_state = "COMPLETE"
    try:
        while max_rounds == 0 or rounds < max_rounds:
            summary = run_cycle(config)
            rounds += 1
            final_state = summary.state
            if max_rounds and rounds >= max_rounds:
                break
            LOG.info("cycle_id=%s next_round_wait=%.1fs", summary.cycle_id, config.interval_seconds)
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        LOG.info("generation loop interrupted by operator after %s cycle(s)", rounds)
        return 0
    if final_state == "CATALOG_UNAVAILABLE":
        return 8
    if final_state == "LLM_UNAVAILABLE":
        return 7
    return 0


def _summary_from_snapshot(cycle_id: str, state: str, snapshots: Any, queue_rows: tuple[dict[str, str], ...], *, detail: str = "") -> CycleSummary:
    return CycleSummary(
        cycle_id, state, len(snapshots.catalog.fields), len(snapshots.catalog.operators), len(snapshots.catalog.datasets),
        len(snapshots.feedback.records), len(snapshots.feedback.positive), len(snapshots.feedback.near_pass),
        len(snapshots.feedback.self_corr_risk), detail=detail, queue_rows=queue_rows,
    )


def _queue_row(candidate: Any, *, model_id: str) -> dict[str, str]:
    settings = candidate.settings
    payload = json.dumps({"expression": candidate.expression, "settings": settings}, ensure_ascii=False, sort_keys=True)
    request_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "candidate_id": hashlib.sha256(("candidate:" + request_hash).encode("utf-8")).hexdigest(),
        "request_hash": request_hash,
        "expression": candidate.expression,
        "alpha_type": settings["alpha_type"], "region": settings["region"], "universe": settings["universe"],
        "delay": str(settings["delay"]), "decay": str(settings["decay"]), "neutralization": settings["neutralization"],
        "truncation": str(settings["truncation"]), "language": settings["language"],
        "data_fields": json.dumps(extract_fields(candidate.expression), ensure_ascii=False),
        "datasets": json.dumps(candidate.datasets, ensure_ascii=False), "operator_family": operator_topology(candidate.expression),
        "exact_hash": exact_hash(candidate.expression),
        "normalized_hash": hashlib.sha256(normalized_expression(candidate.expression).encode("utf-8")).hexdigest(),
        "structure_signature": structure_signature(candidate.expression), "behavior_signature": behavior_signature(candidate.expression),
        "canonical_signature": structure_signature(candidate.expression), "generator_source": candidate.generator_source,
        "parent_template": candidate.parent_seed, "parent_seed": candidate.parent_seed,
        "research_direction": candidate.research_direction, "economic_hypothesis": candidate.hypothesis,
        "economic_rationale": candidate.economic_rationale, "knowledge_refs_json": json.dumps(candidate.knowledge_refs, ensure_ascii=False),
        "feedback_refs_json": json.dumps(candidate.feedback_refs, ensure_ascii=False), "anti_corr_design": candidate.anti_corr_design,
        "expected_turnover_behavior": candidate.expected_turnover_behavior,
        "local_quality_score": str(candidate.local_quality_score), "novelty_score": str(candidate.novelty_score),
        "self_corr_risk_score": str(candidate.self_corr_risk_score), "quality_evidence_json": json.dumps(candidate.quality_evidence, ensure_ascii=False, sort_keys=True),
        "llm_model": model_id, "knowledge_usage_mode": "LIVE_LLM_KNOWLEDGE", "degraded": "false",
        "queue_status": "PENDING_SIMULATION", "alpha_id": "", "retry_count": "0", "last_error_category": "", "last_error": "",
    }


def _event(queue: CandidateCsvQueue, candidate_id: str, event_type: str, detail: str) -> None:
    with queue.writer():
        queue.record_event(candidate_id, event_type, detail)


def _cycle_id() -> str:
    return "cycle_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _log_cycle(cycle_id: str, state: str, **values: Any) -> None:
    details = " ".join(f"{key}={value}" for key, value in values.items() if value not in (None, ""))
    LOG.info("cycle_id=%s state=%s %s", cycle_id, state, details)


# Imports are intentionally local to the pure-domain package, never platform adapters.
from alpha_mining.domain.expression_normalization import behavior_signature, exact_hash, extract_fields, normalized_expression, operator_topology, structure_signature
