#!/usr/bin/env python3
"""Test catalog issue - why fields are empty."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json

print("🔍 Analyzing catalog cache issue...\n")

# 1. Check cache
cache_path = Path(".alpha_operators_cache.json")
cache = json.loads(cache_path.read_text())

print("1️⃣ Current cache status:")
print(f"   Operators: {len(cache.get('operators', []))}")
print(f"   Fields: {len(cache.get('fields', []))}")
print(f"   Last updated: {cache.get('last_updated', 'unknown')}")

if not cache.get('fields'):
    print("\n   ❌ CRITICAL: Fields list is EMPTY!")
    print("   This explains why catalog validation fails.")

# 2. Check what data_mappings need
import sqlite3
con = sqlite3.connect("alpha_state.sqlite3")
cursor = con.cursor()
cursor.execute("""
    SELECT DISTINCT m.dataset_id, m.data_field
    FROM data_mappings m
    ORDER BY m.dataset_id, m.data_field
""")
needed_fields = cursor.fetchall()
con.close()

print(f"\n2️⃣ Data mappings need {len(needed_fields)} unique dataset.field combinations:")
for ds, field in needed_fields[:10]:
    print(f"   {ds}.{field}")
if len(needed_fields) > 10:
    print(f"   ... and {len(needed_fields) - 10} more")

# 3. Check if we can rebuild cache
print("\n3️⃣ Attempting to rebuild catalog cache...")
from alpha_mining.platform.catalog import read_catalog

try:
    operators, fields = read_catalog()
    print(f"   ✅ Successfully fetched catalog:")
    print(f"      Operators: {len(operators)}")
    print(f"      Fields: {len(fields)}")

    if fields:
        print("\n   Sample fields:")
        for field in list(fields.items())[:5]:
            print(f"      {field}")

    # Save it
    import datetime
    new_cache = {
        "operators": operators,
        "fields": fields,
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    cache_path.write_text(json.dumps(new_cache, indent=2))
    print(f"\n   💾 Saved new cache to {cache_path}")

except Exception as e:
    print(f"   ❌ Failed to fetch catalog: {e}")
    import traceback
    traceback.print_exc()
