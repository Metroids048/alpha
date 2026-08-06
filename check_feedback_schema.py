#!/usr/bin/env python3
"""查看feedback表结构"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== feedback表结构 ===")
c.execute("PRAGMA table_info(feedback)")
for row in c.fetchall():
    print(f"  {row[1]:30s} {row[2]:15s} {'NOT NULL' if row[3] else ''}")

print("\n=== feedback表样本数据（最近5条）===")
c.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 5")
cols = [desc[0] for desc in c.description]
for row in c.fetchall():
    print("\n" + "="*60)
    for col, val in zip(cols, row):
        if col == 'payload_json' and val:
            print(f"  {col}: {val[:100]}...")
        else:
            print(f"  {col}: {val}")

conn.close()
