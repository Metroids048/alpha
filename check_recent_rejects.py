#!/usr/bin/env python3
"""查看最新被拒候选的详细原因"""
import sqlite3
from pathlib import Path
import json

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 最近10个被拒候选 ===")
c.execute("""
    SELECT candidate_id, state, quality_reasons_json, checks_json, payload_json, created_at
    FROM candidate_work_items
    WHERE state LIKE 'REJECTED%' OR state='FAR_FAIL'
    ORDER BY created_at DESC
    LIMIT 10
""")

for cid, state, quality_json, checks_json, payload, created in c.fetchall():
    try:
        p = json.loads(payload) if payload else {}
        expr = p.get('expression', '(无)')[:50]
        parent = p.get('parent_template_id', '(无)')[:20]

        quality = json.loads(quality_json) if quality_json else {}
        checks = json.loads(checks_json) if checks_json else {}
    except:
        expr = '(解析失败)'
        parent = '(解析失败)'
        quality = {}
        checks = {}

    print(f"\n{cid[:16]}... @ {created}")
    print(f"  状态: {state}")
    print(f"  quality_reasons: {quality}")
    print(f"  checks: {checks}")
    print(f"  表达式: {expr}...")
    print(f"  父模板: {parent}")

conn.close()
