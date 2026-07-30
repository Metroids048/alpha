#!/usr/bin/env python3
"""Test the full orchestrator flow to see where it breaks."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

print("🧪 Testing full orchestrator flow...\n")

# 1. Check catalog cache
print("1️⃣ Checking catalog cache...")
cache_path = Path(".alpha_operators_cache.json")
if cache_path.exists():
    import json
    cache = json.loads(cache_path.read_text())
    print(f"   ✅ Cache exists: {len(cache.get('fields', []))} fields, {len(cache.get('operators', []))} operators")
    print(f"   📅 Last updated: {cache.get('last_updated', 'unknown')}")
else:
    print("   ❌ Cache missing!")

# 2. Check if orchestrator can load research specs
print("\n2️⃣ Testing orchestrator._research_specs...")
from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.simulate.async_batch import AsyncSimulator

db_path = "alpha_state.sqlite3"
simulator = AsyncSimulator()
orchestrator = FactoryOrchestrator(db_path, simulator)

# Call the private method
import sqlite3
con = sqlite3.connect(db_path)
cursor = con.cursor()
cursor.execute("""
    SELECT
        h.hypothesis_id,
        t.data_category,
        h.mechanism,
        COALESCE(h.horizon, '21d'),
        m.data_field,
        m.dataset_id
    FROM hypotheses h
    JOIN research_topics t ON t.topic_id = h.topic_id
    LEFT JOIN data_mappings m ON m.hypothesis_id = h.hypothesis_id
    WHERE h.status = 'active' AND t.active = 1
""")
raw_specs = cursor.fetchall()
con.close()

print(f"   ✅ Found {len(raw_specs)} raw specs from database")

# 3. Check catalog validation
print("\n3️⃣ Testing catalog validation...")
if raw_specs:
    mappings = [(spec[4], spec[5]) for spec in raw_specs if spec[4] and spec[5]]
    print(f"   📊 {len(mappings)} data mappings to validate")

    # This is what _catalog_unavailable_reason does
    deferred = orchestrator._catalog_unavailable_reason(mappings)
    if deferred:
        print(f"   ❌ CATALOG DEFERRED: {deferred}")
    else:
        print(f"   ✅ Catalog validation passed")

print("\n4️⃣ Testing one generation cycle...")
try:
    summary = orchestrator.generate_and_simulate(max_new=1)
    print(f"   ✅ Cycle completed:")
    print(f"      Generated: {summary.generated}")
    print(f"      Simulated: {summary.simulated}")
    print(f"      Deferred: {summary.deferred_reason}")
    if summary.generated == 0:
        print(f"   ⚠️  No alphas generated - this is the EMPTY_CANDIDATE_BATCH issue")
except Exception as e:
    print(f"   ❌ Error: {e}")
    import traceback
    traceback.print_exc()
