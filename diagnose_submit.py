#!/usr/bin/env python3
"""诊断为什么提交Alpha.py processed=0"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 当前状态分布 ===")
c.execute("SELECT state, COUNT(*) FROM candidate_work_items GROUP BY state")
for state, count in c.fetchall():
    print(f"  {state}: {count}")

print("\n=== PENDING_SIMULATION候选 ===")
c.execute("SELECT COUNT(*) FROM candidate_work_items WHERE state='PENDING_SIMULATION'")
pending = c.fetchone()[0]
print(f"数量: {pending}")

if pending == 0:
    print("\n⚠️  没有待提交的候选！")
    print("原因: 上次运行后所有PENDING都变成了FAR_FAIL")
    print("\n解决方案:")
    print("  1. 先运行: python 生成Alpha.py --once")
    print("  2. 再运行: python 提交Alpha.py --once")
else:
    print(f"\n有 {pending} 个待提交候选，检查提交脚本逻辑...")
    c.execute("""SELECT candidate_id, payload_json, created_at
                 FROM candidate_work_items
                 WHERE state='PENDING_SIMULATION'
                 LIMIT 3""")
    import json
    for cid, payload, created in c.fetchall():
        try:
            p = json.loads(payload) if payload else {}
            expr = p.get('expression', '(无)')
        except:
            expr = '(解析失败)'
        print(f"  {cid[:16]}... {expr[:60]} @ {created}")

conn.close()
