#!/usr/bin/env python3
"""检查research_specs表"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

# 查找所有表
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
all_tables = [r[0] for r in c.fetchall()]
print("所有表:")
for t in all_tables:
    print(f"  - {t}")

# 查找research相关表
research_tables = [t for t in all_tables if 'research' in t.lower() or 'spec' in t.lower()]
print(f"\nresearch相关表: {research_tables}")

# 检查research_specs表
if 'research_specs' in all_tables:
    c.execute("SELECT COUNT(*) FROM research_specs")
    count = c.fetchone()[0]
    print(f"\nresearch_specs记录数: {count}")

    if count > 0:
        c.execute("SELECT topic_id, hypothesis_id, family FROM research_specs LIMIT 5")
        print("\n前5条记录:")
        for row in c.fetchall():
            print(f"  {row}")
else:
    print("\n❌ research_specs表不存在！")

conn.close()
