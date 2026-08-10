"""TEMPORARY pilot generation driver.  Delete after fresh-alpha validation.

Runs the FROZEN production generation path against an ISOLATED queue/database so
the 20 pre-fix PENDING candidates in the real queue stay untouched.

Deliberately keeps every policy knob at its production default:
  pending_limit = 20      (25 would raise portfolio topology_limit 5 -> 6 and
                           contaminate the P-004 diversity verdict)
  portfolio_mode = enforce
  allow_degraded = False

Isolated:  root (queue csv + events), database
Shared:    catalog cache (this run's real sync), knowledge root, LLM config
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.generation.production import ProductionConfig, run_cycle

VAL_ROOT = _ROOT / ".validation_workspace"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1)
    args = parser.parse_args()

    config = ProductionConfig(
        # database is deliberately NOT set: run_cycle only calls
        # initialize_authoritative_database() when config.database is None, and
        # CandidateWorkStore does not create its own schema.  Passing the path
        # explicitly would skip migration and hit missing tables.  The derived
        # path is VAL_ROOT/数据/本地运行产物/数据库/research_memory.sqlite either way.
        root=VAL_ROOT,
        # Fresh cache from this run's sync, written into VAL_ROOT by
        # tmp_validation_driver.py.  Deliberately NOT _ROOT: the repo-root
        # caches remain on their pre-fix snapshot for this validation round.
        catalog_dir=VAL_ROOT,
        knowledge_root=_ROOT / "World quant",
        # every knob below is the production default, stated explicitly so the
        # audit record proves nothing was relaxed for the pilot
        pending_limit=20,
        portfolio_mode="enforce",
        allow_degraded=False,
    )

    print("=== effective configuration ===")
    print(f"  root                  {config.root}")
    print(f"  database_path         {config.database_path}")
    print(f"  queue_path            {config.queue_path}")
    print(f"  events_path           {config.events_path}")
    print(f"  catalog_dir           {config.catalog_dir}")
    print(f"  settings_schema_path  {config.simulation_settings_schema_path}")
    print(f"  knowledge_root        {config.worldquant_root}")
    print(f"  pending_limit         {config.pending_limit}")
    print(f"  portfolio_mode        {config.portfolio_mode}")
    print(f"  allow_degraded        {config.allow_degraded}")
    print(f"  offline_quality_thr   {config.offline_quality_threshold}")

    assert config.pending_limit == 20, "pending_limit must stay at the production default"
    assert config.allow_degraded is False, "degraded fallback must stay off"
    assert config.root != _ROOT, "validation root must be isolated from the real queue"

    for index in range(max(1, args.cycles)):
        print(f"\n=== cycle {index + 1}/{args.cycles} ===")
        try:
            summary = run_cycle(config)
        except Exception as exc:  # noqa: BLE001 - classification is the point
            print(f"  CYCLE FAILED: {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()
            return 3
        print(f"  state            {summary.state}")
        print(f"  detail           {summary.detail}")
        print(f"  catalog          fields={summary.catalog_fields} operators={summary.catalog_operators} datasets={summary.catalog_datasets}")
        print(f"  llm              model={summary.llm_model} candidates={summary.llm_candidates} seeds={summary.v50_seeds}")
        print(f"  enqueued         {summary.enqueued}")
        print(f"  pending_total    {summary.pending_total}")
        if summary.rejections:
            print(f"  rejections       {json.dumps(summary.rejections, sort_keys=True, ensure_ascii=False)}")
        if summary.state not in {"ENQUEUED", "GENERATED"} and summary.enqueued == 0:
            print("  -> no candidates produced this cycle; stopping")
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
