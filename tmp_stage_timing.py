"""Read-only per-stage timing of load_local_snapshots internals."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_mining.generation.snapshots import (  # noqa: E402
    _read_queue,
    load_candidate_inventory,
    load_catalog_snapshot,
    load_feedback_summary,
)

ROOT = Path(".")
DB = ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    t0 = time.perf_counter()
    catalog, source_dir, source, age = load_catalog_snapshot(
        root=ROOT,
        catalog_dir=Path(".validation_workspace"),
        allow_partial_offline=True,
        offline_max_age_hours=336.0,
    )
    print(f"A. load_catalog_snapshot      {time.perf_counter() - t0:8.2f}s "
          f"fields={len(catalog.fields)} source={source}", flush=True)

    t = time.perf_counter()
    queue_rows = _read_queue(ROOT / "待提交Alpha列表.csv")
    print(f"B. _read_queue(csv)           {time.perf_counter() - t:8.2f}s rows={len(queue_rows)}", flush=True)

    t = time.perf_counter()
    events = _read_queue(ROOT / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv")
    print(f"C. _read_queue(events)        {time.perf_counter() - t:8.2f}s rows={len(events)}", flush=True)

    t = time.perf_counter()
    inv = load_candidate_inventory(queue_rows, event_rows=events)
    print(f"D. load_candidate_inventory   {time.perf_counter() - t:8.2f}s", flush=True)

    t = time.perf_counter()
    fb = load_feedback_summary(DB, queue_rows=queue_rows, root=ROOT)
    print(f"E. load_feedback_summary      {time.perf_counter() - t:8.2f}s "
          f"records={len(fb.records)}", flush=True)

    print(f"TOTAL load_local_snapshots    {time.perf_counter() - t0:8.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
