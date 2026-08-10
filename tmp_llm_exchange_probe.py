"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Capture the REAL research-plan LLM exchange so the 6/6 plan rejection is
diagnosed from evidence, not inference.  Issues LLM requests (DeepSeek) but NO
WorldQuant request.  Mutates no cache, no queue, no database row.

Records, per call: prompt char/token size, whether it exceeds the model context,
the returned plan, and the deterministic gate verdict.
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

VAL_ROOT = _ROOT / ".validation_workspace"
OUT = _ROOT / "tmp_llm_exchange.json"


def main() -> int:
    config = ProductionConfig(
        root=VAL_ROOT, catalog_dir=VAL_ROOT, knowledge_root=_ROOT / "World quant",
        pending_limit=20, portfolio_mode="enforce", allow_degraded=False,
    )
    print(f"EFFECTIVE_ROOT={config.root}")
    print(f"EFFECTIVE_DATABASE={config.database_path}")

    snapshots = load_local_snapshots(
        root=config.root, catalog_dir=config.catalog_dir, database=config.database_path,
        queue_path=config.queue_path, allow_partial_offline=True,
        offline_max_age_hours=config.offline_catalog_max_age_hours,
    )

    calls: list[dict] = []
    original = hq.HighQualityGenerator._call_llm

    def traced(self, **kwargs):  # noqa: ANN001, ANN003
        user = str(kwargs.get("user_prompt") or "")
        system = str(kwargs.get("system_prompt") or "")
        entry = {
            "index": len(calls) + 1,
            "system_chars": len(system),
            "user_chars": len(user),
            "approx_tokens": (len(user) + len(system)) // 4,
        }
        try:
            response = original(self, **kwargs)
            entry["ok"] = True
            entry["response_keys"] = sorted(response) if isinstance(response, dict) else None
            entry["response"] = response
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
            calls.append(entry)
            raise
        calls.append(entry)
        print(f"  call#{entry['index']}  user_chars={entry['user_chars']} "
              f"approx_tokens={entry['approx_tokens']} ok={entry['ok']} "
              f"keys={entry.get('response_keys')}")
        return response

    hq.HighQualityGenerator._call_llm = traced  # type: ignore[method-assign]

    # Same wiring production.py:258 uses, so the probe measures the real path.
    from alpha_mining.generation.v50_kernel import V50Kernel
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository
    from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    generator = hq.HighQualityGenerator(
        llm=DeepSeekStructuredLLM(),
        kernel=V50Kernel(seed_pool_size=150),
        knowledge_repository=WorldQuantKnowledgeRepository(config.worldquant_root),
        portfolio_mode=config.portfolio_mode,
        portfolio_limits=config.portfolio_limits,
        portfolio_pending_limit=config.pending_limit,
        settings_contract=SimulationSettingsContract.load(config.simulation_settings_schema_path),
        allow_degraded=config.allow_degraded,
        offline_quality_threshold=config.offline_quality_threshold,
    )

    print("\n=== running ONE research round (LLM live, no WorldQuant) ===")
    try:
        result = generator.generate(
            snapshots, cycle_id="probe", candidates_per_cycle=config.candidates_per_cycle,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  RUN RAISED: {type(exc).__name__}: {exc}")
        result = None

    if result is not None:
        print(f"  approved      {len(getattr(result, 'approved', ()) or ())}")
        print(f"  rejections    {json.dumps(getattr(result, 'rejections', {}) or {}, sort_keys=True)}")

    OUT.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT} ({len(calls)} calls)")

    # Gate verdict on the plan the model actually returned.
    for entry in calls:
        response = entry.get("response")
        if not isinstance(response, dict) or "fields_to_use" not in response:
            continue
        allowed = hq.HighQualityGenerator._research_field_ids(snapshots, [])
        priority = hq.HighQualityGenerator._research_dataset_priority(snapshots, allowed)
        fields = [str(item) for item in (response.get("fields_to_use") or [])]
        resolved = {f: snapshots.catalog.fields.get(f) for f in fields}
        print(f"\n=== PLAN FROM call#{entry['index']} ===")
        print(f"  fields_to_use   {fields}")
        for field, meta in resolved.items():
            in_scope = field in allowed
            print(f"    {field:<48} exists={meta is not None} in_allowed_scope={in_scope} "
                  f"dataset={getattr(meta, 'dataset_id', None)}")
        chosen = {getattr(m, "dataset_id", None) for m in resolved.values() if m is not None}
        print(f"  chosen datasets {sorted(str(d) for d in chosen if d)}")
        print(f"  priority[0]     {priority[0] if priority else None}")
        print(f"  operators       {response.get('operators_to_use')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
