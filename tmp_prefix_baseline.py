"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT
Score the 7 pre-fix queued candidates against the new gate, read-only.
Establishes the COMPARE baseline: how many would the fix have refused locally?
"""
import csv, os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from alpha_mining.generation.high_quality import _unreduced_vector_fields
from alpha_mining.generation.snapshots import load_local_snapshots

snap = load_local_snapshots(
    root=_ROOT / ".validation_workspace",
    allow_partial_offline=True,
    offline_max_age_hours=100000.0,
)

queue = _ROOT / ".validation_workspace" / "待提交Alpha列表.csv"
with queue.open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

pending = [r for r in rows if str(r.get("queue_status") or "") == "PENDING_SIMULATION"]
print(f"pre-fix pending rows: {len(pending)}\n")
bad = 0
for row in pending:
    expr = str(row.get("expression") or "").strip()
    cid = str(row.get("candidate_id") or "")[:16]
    unreduced = _unreduced_vector_fields(expr, snap.catalog)
    if unreduced:
        bad += 1
    print(f"  {cid}  unreduced={list(unreduced) or '-'}")
print(f"\nwould be refused by the new gate: {bad}/{len(pending)}")
