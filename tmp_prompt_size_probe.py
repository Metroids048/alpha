"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Attribute the research-prompt size block by block.  No WorldQuant request, no
LLM request, no mutation.  Answers: which key actually blows the context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.generation import high_quality as hq
from alpha_mining.generation.production import ProductionConfig
from alpha_mining.generation.snapshots import load_local_snapshots
from alpha_mining.generation.v50_kernel import V50Kernel
from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

VAL_ROOT = _ROOT / ".validation_workspace"


def main() -> int:
    config = ProductionConfig(
        root=VAL_ROOT, catalog_dir=VAL_ROOT, knowledge_root=_ROOT / "World quant",
        pending_limit=20, portfolio_mode="enforce", allow_degraded=False,
    )
    print(f"EFFECTIVE_ROOT={config.root}")
    snapshots = load_local_snapshots(
        root=config.root, catalog_dir=config.catalog_dir, database=config.database_path,
        queue_path=config.queue_path, allow_partial_offline=True,
        offline_max_age_hours=config.offline_catalog_max_age_hours,
    )

    generator = hq.HighQualityGenerator(
        llm=None,  # never called: only prompt construction is exercised
        kernel=V50Kernel(seed_pool_size=150),
        knowledge_repository=WorldQuantKnowledgeRepository(config.worldquant_root),
        portfolio_mode=config.portfolio_mode, portfolio_limits=config.portfolio_limits,
        portfolio_pending_limit=config.pending_limit, allow_degraded=config.allow_degraded,
        offline_quality_threshold=config.offline_quality_threshold,
    )

    # Mirror high_quality.py:136-164 exactly.
    from alpha_mining.generation.high_quality import extract_fields
    from alpha_mining.knowledge.worldquant_repository import KnowledgeIntent

    raw_seeds = list(generator.kernel.generate(snapshots))
    seeds = raw_seeds[:5]
    print(f"raw_seeds={len(raw_seeds)}  seeds_used={len(seeds)}")
    fields = tuple(sorted({
        field for seed in seeds
        for field in extract_fields(str(getattr(seed, "expression", "")))
        if field in snapshots.catalog.fields
    }))
    datasets = {snapshots.catalog.fields[f].dataset_id for f in fields}
    dataset = next(iter(datasets)) if len(datasets) == 1 else ""
    knowledge = generator.knowledge_repository.retrieve(
        dataset=dataset, fields=fields,
        mechanism="fundamental hypothesis operator diversity",
        failure_category=" ".join(sorted(snapshots.feedback.failure_counts)),
        intent=KnowledgeIntent.IDEA_GENERATION,
    )
    print(f"knowledge_snippets={len(getattr(knowledge, 'snippets', ()) or ())}")

    text = hq.HighQualityGenerator._research_prompt(snapshots, seeds, knowledge, "probe")
    payload = json.loads(text)
    print(f"\nTOTAL user_prompt chars={len(text)}  approx_tokens={len(text)//4}")
    print("\n=== BLOCK ATTRIBUTION (sorted by size) ===")
    rows = sorted(
        ((k, len(json.dumps(v, ensure_ascii=False))) for k, v in payload.items()),
        key=lambda item: item[1], reverse=True,
    )
    for key, size in rows:
        print(f"  {key:<24} {size:>9} chars  {100.0*size/len(text):5.1f}%")

    catalog = payload.get("catalog") or {}
    if isinstance(catalog, dict):
        print("\n=== catalog sub-blocks ===")
        for key, value in sorted(
            catalog.items(), key=lambda kv: len(json.dumps(kv[1], ensure_ascii=False)), reverse=True,
        ):
            print(f"  catalog.{key:<22} {len(json.dumps(value, ensure_ascii=False)):>9} chars")

    know = payload.get("knowledge")
    if isinstance(know, list) and know:
        sizes = sorted(len(json.dumps(item, ensure_ascii=False)) for item in know)
        print(f"\n=== knowledge: {len(know)} snippets, "
              f"min={sizes[0]} median={sizes[len(sizes)//2]} max={sizes[-1]} chars ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
