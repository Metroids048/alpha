"""唯一 Alpha 生成入口：生成 → 质量闭环 → 待提交Alpha列表.csv。"""

from __future__ import annotations

import argparse

from alpha_mining.factory.quality_workflow import QualityAlphaWorkflow, QualityGenerationConfig
from alpha_mining.generation.feedback import CandidateFeedbackStore
from alpha_mining.generation.service import CandidateGenerationService
from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore


class _UnavailableStructuredLlm:
    """Forces the bridge's documented deterministic fallback without credentials."""

    def generate_json(self, **_kwargs):
        raise RuntimeError("structured LLM provider is unavailable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="质量优先 Alpha 生成（不提交）")
    parser.add_argument("--database", default="research_memory.sqlite")
    parser.add_argument("--output", default="待提交Alpha列表.csv")
    parser.add_argument("--catalog-dir", default="数据/平台缓存")
    parser.add_argument("--knowledge-dir", default="World quant")
    args = parser.parse_args(argv)
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.generator.llm_consultant_bridge import LLMConsultantBridge
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository
    from alpha_mining.offline.metadata import MetadataCache, MetadataCacheError
    from alpha_mining.platform.catalog import ReadOnlyExpressionCatalog
    from alpha_mining.platform.gateway import PlatformGateway

    database = args.database
    try:
        catalog = ReadOnlyExpressionCatalog(MetadataCache.load(args.catalog_dir, max_age_hours=24))
    except MetadataCacheError as exc:
        print(f"CATALOG_UNAVAILABLE: {exc}")
        return 2
    knowledge = WorldQuantKnowledgeRepository(args.knowledge_dir)
    try:
        from alpha_mining.llm import create_runtime_providers

        llm = create_runtime_providers().llm
    except Exception:
        llm = _UnavailableStructuredLlm()
    workflow = QualityAlphaWorkflow(
        generation_service=CandidateGenerationService(
            database,
            generator=LLMConsultantBridge(database=database, llm=llm, knowledge_repository=knowledge),
            catalog=catalog,
            region=str(catalog.metadata.info.get("region") or ""),
            universe=str(catalog.metadata.info.get("universe") or ""),
            delay=catalog.metadata.info.get("delay"),
        ),
        executor=FactoryOrchestrator(database, PlatformGateway(database=database)),
        feedback_store=CandidateFeedbackStore(database),
        ready_store=ReadyAlphaCsvStore(args.output),
        config=QualityGenerationConfig(),
    )
    print(workflow.run_cycle())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
