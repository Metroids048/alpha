"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT
How many catalog fields are numbered/suffixed variants of another field in the
same dataset?  A ratio between two such fields is near-constant by construction.
Read-only, metadata only.
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_mining.generation.snapshots import load_local_snapshots

snap = load_local_snapshots(
    root=Path(".validation_workspace"), catalog_dir=Path(".validation_workspace"),
    allow_partial_offline=True, offline_max_age_hours=100000.0,
)

SUFFIX = re.compile(r"^(?P<base>.+?)_(?:\d+|v\d+)$")
pairs = 0
examples = []
by_ds = {}
for fid, meta in snap.catalog.fields.items():
    by_ds.setdefault(str(meta.dataset_id), set()).add(fid)
for ds, ids in by_ds.items():
    for fid in ids:
        m = SUFFIX.match(fid)
        if m and m.group("base") in ids:
            pairs += 1
            if len(examples) < 12:
                examples.append((ds, m.group("base"), fid))
print(f"catalog fields: {len(snap.catalog.fields)}")
print(f"fields that are a numbered variant of a sibling in the same dataset: {pairs}")
for ds, base, var in examples:
    print(f"  {ds:28} {base}  <->  {var}")
