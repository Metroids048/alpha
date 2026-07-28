#!/usr/bin/env python3
"""Bootstrap research memory with initial topics and hypotheses."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.storage.sqlite_store import SqliteRunLog

# Initialize schema
db_path = "alpha_state.sqlite3"
db = SqliteRunLog(db_path)
db.initialize_schema()
print(f"✅ Schema initialized in {db_path}")

# Check table contents
import sqlite3
con = sqlite3.connect(db_path)
cursor = con.cursor()

cursor.execute("SELECT COUNT(*) FROM research_topics")
topics_count = cursor.fetchone()[0]
print(f"📊 Research topics: {topics_count}")

cursor.execute("SELECT COUNT(*) FROM hypotheses")
hyp_count = cursor.fetchone()[0]
print(f"📊 Hypotheses: {hyp_count}")

cursor.execute("SELECT COUNT(*) FROM data_mappings")
mappings_count = cursor.fetchone()[0]
print(f"📊 Data mappings: {mappings_count}")

if topics_count == 0:
    print("\n⚠️ No research topics configured!")
    print("💡 Need to bootstrap initial research configuration.")
if hyp_count == 0:
    print("⚠️ No hypotheses configured!")
if mappings_count == 0:
    print("⚠️ No data mappings configured!")

con.close()
