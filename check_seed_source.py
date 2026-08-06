#!/usr/bin/env python3
"""查看seed来源和拓扑重复情况"""
import sqlite3
from pathlib import Path
import json

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== hypotheses表（seed种子）===")
c.execute("PRAGMA table_info(hypotheses)")
cols = [row[1] for row in c.fetchall()]
print(f"字段: {cols}")

c.execute("SELECT * FROM hypotheses")
for row in c.fetchall():
    print(f"\n{dict(zip(cols, row))}")

print("\n=== research_topics表 ===")
c.execute("PRAGMA table_info(research_topics)")
cols = [row[1] for row in c.fetchall()]
print(f"字段: {cols}")

c.execute("SELECT * FROM research_topics")
for row in c.fetchall():
    print(f"\n{dict(zip(cols, row))}")

print("\n=== 最近候选的payload分析 ===")
c.execute("""
    SELECT payload_json FROM candidate_work_items
    WHERE created_at > datetime('now', '-2 hours')
    LIMIT 5
""")
for (payload,) in c.fetchall():
    try:
        p = json.loads(payload) if payload else {}
        print(f"\nExpression: {p.get('expression', '(无)')[:60]}")
        print(f"  parent_template_id: {p.get('parent_template_id', '(无)')}")
        print(f"  topology: {p.get('topology', '(无)')}")
    except Exception as e:
        print(f"解析失败: {e}")

conn.close()
