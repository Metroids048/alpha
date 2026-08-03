#!/usr/bin/env python3
"""Debug why orchestrator cannot find research specs."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sqlite3

db_path = "alpha_state.sqlite3"
con = sqlite3.connect(db_path)
cursor = con.cursor()

print("🔍 Checking database contents...\n")

# Check hypotheses
cursor.execute("""
    SELECT h.hypothesis_id, h.topic_id, h.statement_en, h.status
    FROM hypotheses h
    WHERE h.status = 'active'
    LIMIT 5
""")
print("📋 Active hypotheses:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[2][:60]}...")

# Check data_mappings
cursor.execute("""
    SELECT m.mapping_id, m.hypothesis_id, m.dataset_id, m.data_field
    FROM data_mappings m
    LIMIT 10
""")
print("\n🗺️  Data mappings:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]} -> {row[2]}.{row[3]}")

# Now test the actual query from orchestrator._research_specs
print("\n🔬 Testing orchestrator._research_specs query...")
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
    LIMIT 20
""")
specs = cursor.fetchall()
print(f"\n✅ Query returned {len(specs)} specs")
if specs:
    print("\nFirst 5 specs:")
    for spec in specs[:5]:
        print(f"  {spec}")
else:
    print("❌ No specs found! This explains EMPTY_CANDIDATE_BATCH")

con.close()
