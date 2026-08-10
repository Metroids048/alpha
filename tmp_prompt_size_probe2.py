"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT
Measure the real research prompt after the field_type disclosure.
Read-only: no network, no writes.
"""
import json, os
from pathlib import Path

os.environ.setdefault("ALPHA_STATE_ROOT", ".validation_workspace")

from alpha_mining.generation.high_quality import HighQualityGenerator
from alpha_mining.generation.snapshots import load_local_snapshots

snap = load_local_snapshots(root=Path(".validation_workspace"), allow_partial_offline=True, offline_max_age_hours=100000.0)
print("catalog fields:", len(snap.catalog.fields), "datasets:", len(snap.catalog.datasets))


class _S:
    def __init__(self, r): self.ref_id = r; self.text = "k"


class _K:
    snippets = (_S("ref-1"),)


prompt = HighQualityGenerator._research_prompt(snap, [], _K(), "probe")
payload = json.loads(prompt)
fbd = payload["catalog"]["fields_by_dataset"]
sizes = {d: len(v) for d, v in fbd.items()}
types = {}
for entries in fbd.values():
    for e in entries:
        types[e["field_type"]] = types.get(e["field_type"], 0) + 1

print("prompt chars:", len(prompt))
print("fields_by_dataset chars:", len(json.dumps(fbd, ensure_ascii=False)))
print("visible datasets:", len(fbd))
print("visible fields:", sum(sizes.values()))
print("min/max fields per dataset:", min(sizes.values()), max(sizes.values()))
print("visible field_type mix:", types)
print("field_type present on every entry:",
      all("field_type" in e for v in fbd.values() for e in v))
vec_rule = [r for r in payload["plan_requirements"] if "VECTOR" in r]
print("plan_requirements VECTOR rule present:", bool(vec_rule))
