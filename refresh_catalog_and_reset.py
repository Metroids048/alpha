#!/usr/bin/env python3
"""Force-refresh catalog caches and reset pipeline loop state.
Handles RATE_LIMITED circuit-breaker and 429 with automatic recovery.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env
load_workspace_env(_ROOT / ".env")

import os

print("=" * 60)
print("Step 1 – Verify credentials are present")
username = os.environ.get("WQ_USERNAME", "").strip()
password = os.environ.get("WQ_PASSWORD", "")
if not username or not password:
    print("ERROR: WQ_USERNAME or WQ_PASSWORD missing in .env")
    sys.exit(1)
print(f"  WQ_USERNAME : {username}")
print(f"  WQ_PASSWORD : {'*' * len(password)}")

# ---------------------------------------------------------------
print()
print("Step 2 – Check and recover access-controller circuit state")
from alpha_mining.platform.access import PlatformAccessController, _parse_time
from datetime import datetime, timezone

controller = PlatformAccessController("research_memory.sqlite", "worldquant_api.lock")
state = controller.status()
print(f"  circuit state    : {state.state}")
print(f"  retry_after_until: {state.retry_after_until or 'none'}")
print(f"  recovery_attempts: {state.recovery_attempts}")

if state.state == "RATE_LIMITED":
    until = _parse_time(state.retry_after_until)
    now = datetime.now(timezone.utc)
    if until and now < until:
        wait = int((until - now).total_seconds()) + 5
        print(f"  Rate limit still active. Waiting {wait}s …")
        time.sleep(wait)
    else:
        print("  Rate limit interval has expired – will issue recovery probe.")
elif state.state == "HALF_OPEN":
    # A probe is in flight from a previous crash. Force reset to CLOSED directly.
    import sqlite3
    with sqlite3.connect("research_memory.sqlite") as con:
        con.execute(
            "UPDATE platform_access_state SET state='CLOSED', reason='manual_reset', "
            "retry_after_until=NULL, recovery_attempts=0, updated_at=? WHERE singleton=1",
            (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
        )
    print("  Stuck HALF_OPEN – reset to CLOSED manually.")

# ---------------------------------------------------------------
print()
print("Step 3 – Authenticate and issue recovery probe if needed")
from alpha_mining.platform.client import ReadOnlyPlatformClient

client = ReadOnlyPlatformClient(
    state_path=".wq_auth_state.json",
    database="research_memory.sqlite",
    lock_path="worldquant_api.lock",
    min_interval=3.0,
    timeout=60.0,
)
client.authenticate()

# Refresh state after potential wait
state = controller.status()
recovery_probe = state.state == "RATE_LIMITED"  # retry_after should now be past
try:
    identity = client.fetch_identity(recovery_probe=recovery_probe)
    print(f"  Auth probe OK: {identity.get('username') or identity.get('email')}")
except Exception as e:
    print(f"  Identity probe failed: {e}")
    # Try a direct DB reset and one more attempt
    import sqlite3
    with sqlite3.connect("research_memory.sqlite") as con:
        con.execute(
            "UPDATE platform_access_state SET state='CLOSED', reason='manual_reset_emergency', "
            "retry_after_until=NULL, recovery_attempts=0, updated_at=? WHERE singleton=1",
            (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
        )
    print("  Emergency reset to CLOSED. Retrying identity probe …")
    time.sleep(5)
    try:
        identity = client.fetch_identity()
        print(f"  Auth probe OK: {identity.get('username') or identity.get('email')}")
    except Exception as e2:
        print(f"  FAILED after reset: {e2}")
        sys.exit(2)

# Verify circuit is now CLOSED
state = controller.status()
print(f"  circuit state now: {state.state}")
if state.state not in {"CLOSED"}:
    print(f"  WARNING: unexpected state {state.state}, continuing anyway …")

# ---------------------------------------------------------------
print()
print("Step 4 – Run catalog sync (may take 1–2 minutes)")
from alpha_mining.platform.catalog import PlatformCatalogSynchronizer

MAX_ATTEMPTS = 4
for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"  attempt {attempt}/{MAX_ATTEMPTS} …")
    try:
        syncer = PlatformCatalogSynchronizer(_ROOT, page_size=20)
        counts = syncer.sync(client, region="USA", universe="TOP3000", delay=1)
        print(f"  datasets   : {counts.get('datasets', '?')}")
        print(f"  data_fields: {counts.get('data_fields', '?')}")
        print(f"  operators  : {counts.get('operators', '?')}")
        print("  Catalog files refreshed ✓")
        break
    except Exception as e:
        msg = str(e)
        if "429" in msg:
            wait = 90 if attempt < 3 else 240
            print(f"  429 hit – waiting {wait}s, then issuing recovery probe …")
            time.sleep(wait)
            # Reset circuit and re-probe before next attempt
            import sqlite3
            with sqlite3.connect("research_memory.sqlite") as con:
                con.execute(
                    "UPDATE platform_access_state SET state='CLOSED', reason='429_reset', "
                    "retry_after_until=NULL, recovery_attempts=0, updated_at=? WHERE singleton=1",
                    (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
                )
            try:
                client.fetch_identity()
            except Exception:
                pass
        else:
            print(f"  CATALOG SYNC FAILED: {e}")
            sys.exit(3)
else:
    print("  Exhausted all retry attempts. Try again in a few minutes.")
    sys.exit(4)

# ---------------------------------------------------------------
print()
print("Step 5 – Verify cache files are now fresh")
for fname in (".alpha_datafields_cache.json", ".alpha_datasets_cache.json", ".alpha_operators_cache.json"):
    p = _ROOT / fname
    if not p.exists():
        print(f"  MISSING: {fname}")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    age_h = (time.time() - float(d.get("cached_at", 0))) / 3600
    print(f"  {fname}: age={age_h:.2f}h  {'✓' if age_h < 1 else '⚠ WARN'}")

# ---------------------------------------------------------------
print()
print("Step 6 – Reset pipeline_loop_state.json failure counters")
state_path = _ROOT / "pipeline_loop_state.json"
if state_path.exists():
    s = json.loads(state_path.read_text(encoding="utf-8"))
    old = s.get("consecutive_cycle_failures", 0)
    s["consecutive_cycle_failures"] = 0
    s["consecutive_failures"] = 0
    s["last_outcome_category"] = ""
    s["last_exception"] = ""
    s["last_failure_category"] = ""
    state_path.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  consecutive_cycle_failures: {old} → 0 ✓")

# ---------------------------------------------------------------
print()
print("=" * 60)
print("ALL DONE – pipeline can now resume.")
print()
print("Start the pipeline with:")
print("  python run_pipeline_loop.py")
print("=" * 60)
