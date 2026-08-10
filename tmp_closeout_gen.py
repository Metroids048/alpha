"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT

FINAL CLOSEOUT: generate at most 5 fresh candidates on the frozen HEAD.

Not the batch harness: there is no target count to fill and no acceptance
sweep.  The cap is the point -- it stops at 5 new candidate_ids, or earlier if
the operator's own stop condition is met, and it never reruns to "observe more".

Same frozen production path and production knobs as the other drivers
(pending_limit=20 / portfolio_mode=enforce / allow_degraded=False), asserted
before the first cycle.  Fresh is decided by candidate_id: the ids present
before cycle 1 are recorded and excluded, so the 14 rows generated before the
py-fastplus arming (13:03Z) and the group-axis fix (13:17Z) cannot be counted.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.generation.production import ProductionConfig, run_cycle

VAL_ROOT = _ROOT / ".validation_workspace"
QUEUE = VAL_ROOT / "待提交Alpha列表.csv"

_MAX_FRESH = 5


def _ids() -> set[str]:
    if not QUEUE.exists():
        return set()
    with QUEUE.open(encoding="utf-8-sig", newline="") as handle:
        return {str(r.get("candidate_id") or "") for r in csv.DictReader(handle)}


def _rows_for(ids: set[str]) -> list[dict[str, str]]:
    with QUEUE.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if str(r.get("candidate_id") or "") in ids]
    rows.sort(key=lambda item: str(item.get("created_at") or ""))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-cycles", type=int, default=10)
    args = parser.parse_args()

    config = ProductionConfig(
        root=VAL_ROOT,
        catalog_dir=VAL_ROOT,
        knowledge_root=_ROOT / "World quant",
        pending_limit=20,
        portfolio_mode="enforce",
        allow_degraded=False,
    )
    assert config.pending_limit == 20, "pending_limit must stay at the production default"
    assert config.allow_degraded is False, "degraded fallback must stay off"
    assert config.portfolio_mode == "enforce", "portfolio guard must stay on"
    assert config.root != _ROOT, "validation root must be isolated from the real queue"

    baseline = _ids()
    print(f"=== CLOSEOUT GEN: cap={_MAX_FRESH} fresh, max_cycles={args.max_cycles} ===")
    print(f"  pre-existing candidate_ids (excluded): {len(baseline)}")

    totals: Counter[str] = Counter()
    started = time.monotonic()

    for index in range(args.max_cycles):
        try:
            summary = run_cycle(config)
        except Exception as exc:  # noqa: BLE001
            print(f"[cycle {index+1}] FAILED {type(exc).__name__}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            break
        for code, count in (summary.rejections or {}).items():
            totals[code] += int(count)
        fresh = _ids() - baseline
        print(
            f"[cycle {index+1}] state={summary.state} enqueued={summary.enqueued} "
            f"fresh_total={len(fresh)} pending_total={summary.pending_total} "
            f"rejections={json.dumps(summary.rejections or {}, sort_keys=True)}",
            flush=True,
        )
        if len(fresh) >= _MAX_FRESH:
            print(f"  -> cap of {_MAX_FRESH} fresh candidates reached", flush=True)
            break

    fresh = _ids() - baseline
    elapsed = (time.monotonic() - started) / 60.0
    print(f"\n=== CLOSEOUT GEN summary ===")
    print(f"  fresh_candidates {len(fresh)}")
    print(f"  elapsed_minutes  {elapsed:.1f}")
    print(f"  rejection_totals {json.dumps(dict(sorted(totals.items())), sort_keys=True)}")
    for row in _rows_for(fresh):
        print(f"\n  candidate_id  {str(row.get('candidate_id') or '')[:16]}")
        print(f"  created_at    {row.get('created_at')}")
        print(f"  expression    {row.get('expression')}")
        print(f"  datasets      {row.get('datasets')}")
        print(f"  fields        {row.get('data_fields')}")
        print(f"  local_quality {row.get('local_quality_score')}")
        print(f"  hypothesis    {str(row.get('economic_hypothesis') or '')[:200]}")
    if fresh:
        first = _rows_for(fresh)[0].get("created_at")
        print(f"\n  gates --since  {first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
