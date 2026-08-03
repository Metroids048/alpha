"""唯一 Alpha 生成入口：生成 → 质量闭环 → 待提交Alpha列表.csv。"""

from __future__ import annotations

import argparse

from alpha_mining.factory.quality_workflow import QualityAlphaWorkflow, QualityGenerationConfig
from alpha_mining.generation.feedback import CandidateFeedbackStore
from alpha_mining.generation.service import CandidateGenerationService
from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="质量优先 Alpha 生成（不提交）")
    parser.add_argument("--database", default="research_memory.sqlite")
    parser.add_argument("--output", default="待提交Alpha列表.csv")
    args = parser.parse_args(argv)
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.platform.gateway import PlatformGateway

    database = args.database
    workflow = QualityAlphaWorkflow(
        generation_service=CandidateGenerationService(database),
        executor=FactoryOrchestrator(database, PlatformGateway(database=database)),
        feedback_store=CandidateFeedbackStore(database),
        ready_store=ReadyAlphaCsvStore(args.output),
        config=QualityGenerationConfig(),
    )
    print(workflow.run_cycle())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
