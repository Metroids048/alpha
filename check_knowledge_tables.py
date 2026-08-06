#!/usr/bin/env python3
"""检查knowledge相关表"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

# 查找knowledge相关表
c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%knowledge%'")
knowledge_tables = [r[0] for r in c.fetchall()]
print("Knowledge相关表:")
for t in knowledge_tables:
    print(f"  - {t}")

# 如果有knowledge_base表，检查数据
if 'knowledge_base' in knowledge_tables:
    c.execute("SELECT COUNT(*) FROM knowledge_base")
    count = c.fetchone()[0]
    print(f"\nknowledge_base记录数: {count}")

    if count > 0:
        c.execute("SELECT * FROM knowledge_base LIMIT 3")
        print("\n前3条记录:")
        for row in c.fetchall():
            print(f"  {row}")
    else:
        print("\n❌ knowledge_base表为空！")

        # 检查表结构
        c.execute("PRAGMA table_info(knowledge_base)")
        print("\n表结构:")
        for col in c.fetchall():
            print(f"  {col}")
else:
    print("\n❌ knowledge_base表不存在！")

conn.close()
