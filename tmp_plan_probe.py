"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT
Inspect what the plan step selects now: does it pick VECTOR fields, and does it
whitelist a vec_* reducer alongside them?  One DeepSeek plan call, no WorldQuant
request, no cache/queue/db mutation.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
from alpha_mining.common import load_workspace_env
load_workspace_env(_ROOT / ".env")

from alpha_mining.generation import high_quality as hq
from alpha_mining.generation.snapshots import load_local_snapshots

VAL_ROOT = _ROOT / ".validation_workspace"
snap = load_local_snapshots(
    root=VAL_ROOT, catalog_dir=VAL_ROOT,
    allow_partial_offline=True, offline_max_age_hours=100000.0,
)

allowed = hq.HighQualityGenerator._research_field_ids(snap, [])
vec_visible = [
    f for f in allowed
    if str(getattr(snap.catalog.fields[f], "field_type", "")).upper() == "VECTOR"
]
print(f"visible fields={len(allowed)}  of which VECTOR={len(vec_visible)}")
prio = hq.HighQualityGenerator._research_dataset_priority(snap, allowed)
print(f"dataset_priority[:5]={list(prio)[:5]}")
top = prio[0]
top_fields = [f for f in allowed if snap.catalog.fields[f].dataset_id == top]
top_vec = [f for f in top_fields
           if str(getattr(snap.catalog.fields[f], "field_type", "")).upper() == "VECTOR"]
print(f"priority dataset {top!r}: fields={len(top_fields)} VECTOR={len(top_vec)}")
print(f"  -> priority dataset is {100*len(top_vec)/max(1,len(top_fields)):.0f}% VECTOR")
