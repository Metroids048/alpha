"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT

Batch generation driver.  Same frozen production path and same production knobs
as tmp_pilot_driver.py; the only difference is that an empty cycle does not stop
the run, because a batch needs a target count rather than a first result.

Nothing here relaxes a gate: pending_limit / portfolio_mode / allow_degraded stay
at their production defaults and are asserted before the first cycle.
"""

from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, required=True,
                        help="stop once pending_total reaches this")
    parser.add_argument("--max-cycles", type=int, default=60)
    parser.add_argument("--label", default="BATCH")
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
    assert config.root != _ROOT, "validation root must be isolated from the real queue"

    print(f"=== {args.label}: target pending_total={args.target} max_cycles={args.max_cycles} ===")
    totals: Counter[str] = Counter()
    enqueued_total = 0
    started = time.monotonic()

    for index in range(args.max_cycles):
        try:
            summary = run_cycle(config)
        except Exception as exc:  # noqa: BLE001
            print(f"[cycle {index+1}] FAILED {type(exc).__name__}: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            break
        enqueued_total += int(summary.enqueued or 0)
        for code, count in (summary.rejections or {}).items():
            totals[code] += int(count)
        print(
            f"[cycle {index+1}] state={summary.state} enqueued={summary.enqueued} "
            f"pending_total={summary.pending_total} "
            f"rejections={json.dumps(summary.rejections or {}, sort_keys=True)}",
            flush=True,
        )
        if int(summary.pending_total or 0) >= args.target:
            print(f"  -> target reached at cycle {index+1}", flush=True)
            break

    elapsed = (time.monotonic() - started) / 60.0
    print(f"\n=== {args.label} summary ===")
    print(f"  enqueued_total   {enqueued_total}")
    print(f"  elapsed_minutes  {elapsed:.1f}")
    print(f"  rejection_totals {json.dumps(dict(sorted(totals.items())), sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
