#!/usr/bin/env python3
"""分析拓扑重复问题"""
import sqlite3
from pathlib import Path
import json

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 当前seed模板 ===")
c.execute("SELECT template_id, topology, created_at FROM alpha_templates ORDER BY created_at DESC LIMIT 10")
templates = []
for tid, topo, created in c.fetchall():
    templates.append((tid, topo, created))
    print(f"{tid[:20]}... | {topo} | {created}")

print(f"\n总共 {len(templates)} 个模板")

print("\n=== 已存在的拓扑（去重） ===")
c.execute("SELECT DISTINCT topology FROM alpha_templates")
unique_topos = [row[0] for row in c.fetchall()]
print(f"唯一拓扑数: {len(unique_topos)}")
for t in unique_topos[:20]:
    print(f"  {t}")

print("\n=== 最近生成候选使用的拓扑 ===")
c.execute("""
    SELECT payload_json FROM candidate_work_items
    WHERE created_at > datetime('now', '-1 hour')
    ORDER BY created_at DESC
    LIMIT 20
""")
candidate_topos = set()
for (payload,) in c.fetchall():
    try:
        p = json.loads(payload) if payload else {}
        topo = p.get('topology')
        if topo:
            candidate_topos.add(topo)
    except:
        pass

print(f"最近1小时候选拓扑数: {len(candidate_topos)}")
for t in sorted(candidate_topos)[:10]:
    print(f"  {t}")

print(f"\n重复度: {len(candidate_topos)} / {len(unique_topos)} = {len(candidate_topos)/max(len(unique_topos),1)*100:.1f}%")

conn.close()
