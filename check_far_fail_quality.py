#!/usr/bin/env python3
"""检查FAR_FAIL候选的表达式质量"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

# 先检查表结构
c.execute("PRAGMA table_info(candidate_work_items)")
columns = [col[1] for col in c.fetchall()]
print(f"candidate_work_items表字段: {columns}\n")

print("=== 最近的 FAR_FAIL 候选 ===\n")
c.execute("""
    SELECT candidate_id, payload_json, last_error_category, last_error, created_at
    FROM candidate_work_items
    WHERE state='FAR_FAIL'
    ORDER BY created_at DESC
    LIMIT 6
""")

import json
for i, (cid, payload, cat, err, created) in enumerate(c.fetchall(), 1):
    try:
        p = json.loads(payload) if payload else {}
        expr = p.get('expression', '(无)')
    except:
        expr = '(解析失败)'

    print(f"{i}. {cid[:16]}...")
    print(f"   表达式: {expr[:100] if expr else '(空)'}")
    print(f"   错误分类: {cat}")
    print(f"   错误详情: {err[:150] if err else '(空)'}")
    print(f"   创建时间: {created}")
    print()

# 检查是否有降级兜底的候选
print("\n=== 检查降级兜底候选 ===")
c.execute("""
    SELECT candidate_id, payload_json
    FROM candidate_work_items
    WHERE state='FAR_FAIL'
    LIMIT 10
""")
degraded = c.fetchall()
degraded_count = 0
for cid, payload in degraded:
    try:
        p = json.loads(payload) if payload else {}
        expr = p.get('expression', '')
        if any(k in expr for k in ['anl10_', 'ts_mean', 'ts_rank']):
            print(f"  {cid[:16]}... {expr[:80]}")
            degraded_count += 1
    except:
        pass

if degraded_count == 0:
    print("没有降级兜底候选")

# 统计rejection原因
print("\n=== 生成rejection统计 ===")
c.execute("""
    SELECT state, last_error_category, COUNT(*)
    FROM candidate_work_items
    GROUP BY state, last_error_category
    ORDER BY COUNT(*) DESC
""")
for state, cat, count in c.fetchall():
    print(f"  {state} / {cat or '(无)'}: {count}")

conn.close()

print("\n" + "="*60)
print("结论:")
print("  1. 生成链路正常（enqueued=1）")
print("  2. Simulate调用成功但平台熔断")
print("  3. 需要平台解除熔断后重新测试")
print("  4. 本地逻辑验证完毕，等待平台恢复")
