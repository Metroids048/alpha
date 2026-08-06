#!/usr/bin/env python3
"""列出数据库中所有表"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 数据库中的所有表 ===")
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = c.fetchall()
for (tbl,) in tables:
    c.execute(f"SELECT COUNT(*) FROM {tbl}")
    cnt = c.fetchone()[0]
    print(f"  {tbl:40s} : {cnt:6d} 行")

conn.close()
