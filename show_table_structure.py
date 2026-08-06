#!/usr/bin/env python3
"""查看candidate_work_items表结构"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== candidate_work_items 表结构 ===")
c.execute("PRAGMA table_info(candidate_work_items)")
for row in c.fetchall():
    print(f"  {row[1]} ({row[2]})")

conn.close()
