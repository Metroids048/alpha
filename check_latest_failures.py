#!/usr/bin/env python3
"""检查最新的FAR_FAIL详情"""
import sqlite3
import json
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 最新3个FAR_FAIL详情 ===\n")
c.execute("""
    SELECT candidate_id, payload_json, last_error, metrics_json, updated_at
    FROM candidate_work_items
    WHERE state='FAR_FAIL'
    ORDER BY updated_at DESC LIMIT 3
""")

for cid, payload, err, metrics, updated in c.fetchall():
    try:
        p = json.loads(payload) if payload else {}
        expr = p.get('expression', '(无)')
    except:
        expr = '(解析失败)'

    print(f"候选: {cid[:16]}... @ {updated}")
    print(f"表达式: {expr}")
    print(f"错误信息: {err[:300] if err else '(空)'}")

    if metrics:
        try:
            m = json.loads(metrics)
            print(f"平台指标: {json.dumps(m, indent=2)}")
        except:
            print(f"指标(原始): {metrics[:200]}")
    else:
        print("指标: (空)")

    print("\n" + "="*60 + "\n")

# 统计错误类型
print("=== 错误类型分布 ===")
c.execute("""
    SELECT
        CASE
            WHEN last_error LIKE '%CircuitOpen%' THEN 'CircuitOpen(熔断)'
            WHEN last_error LIKE '%Sharpe%' THEN 'Quality_Check_Failed(质量不达标)'
            ELSE 'Other'
        END as error_type,
        COUNT(*)
    FROM candidate_work_items
    WHERE state='FAR_FAIL'
    GROUP BY error_type
""")

for error_type, count in c.fetchall():
    print(f"  {error_type}: {count}")

conn.close()

print("\n结论:")
print("  - 如果全是CircuitOpen: 平台仍在熔断，需要扫脸")
print("  - 如果有Quality_Check_Failed: 需要分析具体指标并优化生成策略")
