"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Read-only measurement of the research-plan scope the LLM is handed, to explain
why 6/6 plans died on PLAN_DATASET_CONCENTRATION / PLAN_CROSS_DATASET /
PLAN_UNKNOWN_FIELD against the fresh 297-dataset catalog.

Issues no WorldQuant request and no LLM request. Mutates nothing.
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

VAL_ROOT = _ROOT / ".validation_workspace"


def main() -> int:
    config = ProductionConfig(
        root=VAL_ROOT, catalog_dir=VAL_ROOT, knowledge_root=_ROOT / "World quant",
        pending_limit=20, portfolio_mode="enforce", allow_degraded=False,
    )
    print(f"EFFECTIVE_ROOT={config.root}")
    print(f"EFFECTIVE_DATABASE={config.database_path}")
    print(f"CATALOG_DIR={config.catalog_dir}")

    # Rebuild exactly what run_cycle builds, without calling the LLM.
    from alpha_mining.generation.snapshots import load_local_snapshots

    try:
        snapshots = load_local_snapshots(
            root=config.root,
            catalog_dir=config.catalog_dir,
            database=config.database_path,
            queue_path=config.queue_path,
            allow_partial_offline=True,
            offline_max_age_hours=config.offline_catalog_max_age_hours,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"SNAPSHOT_LOAD_FAILED: {type(exc).__name__}: {exc}")
        return 3

    catalog = snapshots.catalog
    print("\n=== CATALOG ===")
    print(f"  source          {catalog.info.get('source')!r}")
    print(f"  fields          {len(catalog.fields)}")
    print(f"  operators       {len(catalog.operators)}")
    datasets_in_fields = {meta.dataset_id for meta in catalog.fields.values()}
    print(f"  datasets(fields) {len(datasets_in_fields)}")

    seeds: list = []
    allowed = hq.HighQualityGenerator._research_field_ids(snapshots, seeds)
    print("\n=== PLAN SCOPE (allowed_plan_fields) ===")
    print(f"  allowed_plan_fields   {len(allowed)}")
    by_dataset: dict[str, list[str]] = {}
    for field in allowed:
        meta = catalog.fields.get(field)
        if meta is None:
            continue
        by_dataset.setdefault(meta.dataset_id, []).append(field)
    print(f"  datasets represented  {len(by_dataset)}")
    sizes = sorted((len(v) for v in by_dataset.values()))
    if sizes:
        print(f"  fields/dataset min={sizes[0]} median={sizes[len(sizes)//2]} max={sizes[-1]}")

    priority = hq.HighQualityGenerator._research_dataset_priority(snapshots, allowed)
    print("\n=== DATASET PRIORITY ===")
    print(f"  len(priority)   {len(priority)}")
    print(f"  priority[0]     {priority[0] if priority else None!r}")
    print(f"  priority[:8]    {list(priority[:8])}")
    if priority:
        head = priority[0]
        print(f"  fields available under priority[0]: {len(by_dataset.get(head, []))}")
        print(f"  sample: {sorted(by_dataset.get(head, []))[:12]}")

    # The concentration gate demands priority[0]; is it even reachable?
    print("\n=== GATE REACHABILITY ===")
    print(f"  concentration gate active (len(priority)>=3): {len(priority) >= 3}")
    if priority and not by_dataset.get(priority[0]):
        print("  !! priority[0] has ZERO fields in allowed_plan_fields -> gate is unsatisfiable")

    payload = {
        "datasets": sorted(by_dataset),
        "dataset_priority": list(priority),
        "fields_by_dataset": {k: sorted(v) for k, v in sorted(by_dataset.items())},
        "operators": sorted(set(catalog.operators) - hq._GHOST_OPERATORS),
    }
    text = json.dumps(payload, ensure_ascii=False)
    print("\n=== PROMPT CATALOG BLOCK SIZE ===")
    print(f"  json chars      {len(text)}")
    print(f"  approx tokens   {len(text)//4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
