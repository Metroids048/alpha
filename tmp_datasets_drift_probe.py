"""TEMPORARY read-only datasets drift probe.  UNTRACKED / NOT_FOR_COMMIT.

Answers exactly one question: which dataset metadata fields does the platform
mutate between two catalog syncs?  CatalogCheckpointStale fired on
datasets_hash while dataset_ids_hash matched, so the ID set is identical and
some per-record field moved.

Costs 6 requests (297 datasets / page_size 50).  Writes nothing: it never
touches the checkpoint, never writes a production cache, and reuses the same
ReadOnlyPlatformClient path the driver uses so the access controller still
records the requests.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.catalog import _dataset_fingerprint, _stable_hash
from alpha_mining.platform.client import ReadOnlyPlatformClient

VAL_ROOT = _ROOT / ".validation_workspace"
DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
STATE = _ROOT / ".wq_auth_state.json"
MANIFEST = VAL_ROOT / ".alpha_catalog_sync_checkpoint" / "manifest.json"


def main() -> int:
    print("=== provenance ===")
    print(f"  EFFECTIVE_ROOT       = {_ROOT}")
    print(f"  EFFECTIVE_DATABASE   = {DB}")
    print(f"  MANIFEST             = {MANIFEST}")
    print(f"  MANIFEST_EXISTS      = {MANIFEST.exists()}")
    print()
    if not MANIFEST.exists():
        print("BLOCKED: no manifest to diff against")
        return 2

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stored = {str(row.get("id")): row for row in manifest["datasets"]}

    controller = PlatformAccessController(str(DB), str(LOCK))
    s = controller.status()
    print(f"  circuit before       = {s.state} attempts={s.recovery_attempts}/{s.max_auto_recoveries}")
    if s.state != "CLOSED":
        print("BLOCKED: circuit is not CLOSED; refusing to send requests")
        return 3

    client = ReadOnlyPlatformClient(
        state_path=str(STATE), database=str(DB), lock_path=str(LOCK),
        min_interval=3.0, timeout=60.0,
    )
    client.authenticate()

    base = {
        "instrumentType": "EQUITY",
        "region": manifest["region"],
        "universe": manifest["universe"],
        "delay": int(manifest["delay"]),
    }
    page_size = int(manifest["page_size"])
    live_rows: list[dict] = []
    offset = 0
    while True:
        payload = client.list_datasets({**base, "limit": page_size, "offset": offset})
        results = payload.get("results") or []
        live_rows.extend(results)
        count = int(payload.get("count") or 0)
        offset += page_size
        if offset >= count or not results:
            break
    print(f"  live datasets        = {len(live_rows)}  (stored {len(stored)})")
    print()

    live = {str(row.get("id")): row for row in live_rows}
    print("=== hash comparison ===")
    print(f"  stored datasets_hash = {manifest['datasets_hash']}")
    print(f"  live   datasets_hash = {_stable_hash(_dataset_fingerprint(live_rows))}")
    stored_ids = sorted(stored)
    live_ids = sorted(live)
    print(f"  id sets identical    = {stored_ids == live_ids}")
    print(f"  only in stored       = {[i for i in stored_ids if i not in live][:8]}")
    print(f"  only in live         = {[i for i in live_ids if i not in stored][:8]}")
    print()

    changed_fields: collections.Counter[str] = collections.Counter()
    changed_datasets: set[str] = set()
    samples: dict[str, list[str]] = collections.defaultdict(list)
    for dataset_id in stored_ids:
        if dataset_id not in live:
            continue
        before, after = stored[dataset_id], live[dataset_id]
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changed_fields[key] += 1
                changed_datasets.add(dataset_id)
                if len(samples[key]) < 3:
                    samples[key].append(
                        f"{dataset_id}: {before.get(key)!r} -> {after.get(key)!r}"
                    )

    print("=== per-field drift (stored manifest vs live) ===")
    print(f"  datasets with any change = {len(changed_datasets)}/{len(stored_ids)}")
    if not changed_fields:
        print("  (no field changed)")
    for key, n in changed_fields.most_common():
        print(f"  {key:24s} changed in {n:3d}/{len(stored_ids)} datasets")
        for line in samples[key]:
            print(f"      {line[:150]}")
    print()

    volatile = {"alphaCount", "userCount", "valueScore"}
    structural = {k for k in changed_fields if k not in volatile}
    print("=== verdict ===")
    print(f"  volatile counters changed   = {sorted(k for k in changed_fields if k in volatile)}")
    print(f"  non-counter fields changed  = {sorted(structural)}")

    def _strip(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in row.items() if k not in volatile} for row in rows]

    stored_stripped = _stable_hash(_dataset_fingerprint(_strip(list(stored.values()))))
    live_stripped = _stable_hash(_dataset_fingerprint(_strip(live_rows)))
    print(f"  hash ignoring counters match = {stored_stripped == live_stripped}")

    fc_changed = [
        f"{i}: {stored[i].get('fieldCount')} -> {live[i].get('fieldCount')}"
        for i in stored_ids
        if i in live and stored[i].get("fieldCount") != live[i].get("fieldCount")
    ]
    print(f"  fieldCount changes           = {len(fc_changed)} {fc_changed[:5]}")
    print(f"  circuit after                = {controller.status().state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
