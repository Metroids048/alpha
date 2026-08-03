#!/usr/bin/env python3
"""Fix data_mappings with real fields from catalog."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sqlite3
from datetime import datetime, timezone

db_path = "alpha_state.sqlite3"
con = sqlite3.connect(db_path)
cursor = con.cursor()

print("🔧 Fixing data_mappings with real catalog fields...\n")

# 1. Clear old invalid mappings
cursor.execute("DELETE FROM data_mappings")
print(f"✅ Cleared old mappings")

# 2. Get all active hypotheses
cursor.execute("""
    SELECT hypothesis_id FROM hypotheses WHERE status='active'
""")
hypotheses = [row[0] for row in cursor.fetchall()]
print(f"📋 Found {len(hypotheses)} active hypotheses")

# 3. Real fields from catalog
real_fields = [
    ('analyst10', 'anl10_analyst_innovation_eps_innovate_increase_fy1'),
    ('analyst11', 'anl11_1e'),
]

# 4. Create mappings for each hypothesis
count = 0
now = datetime.now(timezone.utc).isoformat()
for hyp_id in hypotheses:
    for dataset_id, data_field in real_fields:
        mapping_id = f"{hyp_id}_{dataset_id}_{data_field}"
        cursor.execute("""
            INSERT INTO data_mappings (mapping_id, hypothesis_id, dataset_id, data_field, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (mapping_id, hyp_id, dataset_id, data_field, now))
        count += 1

con.commit()
con.close()

print(f"✅ Created {count} valid data mappings")
print(f"\n🚀 Pipeline should now be able to generate alphas!")
