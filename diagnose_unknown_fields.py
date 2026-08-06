#!/usr/bin/env python3
"""诊断UNKNOWN_FIELD问题：检查被拒候选使用了哪些不在catalog中的字段"""

import json
import sqlite3
from pathlib import Path

# 加载catalog
catalog_path = Path(".alpha_datafields_cache.json")
catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
known_fields = {row["id"] for row in catalog["rows"]}
print(f"✓ Catalog包含 {len(known_fields)} 个字段")

# 加载被拒候选
db_path = Path("research_memory.sqlite")
if not db_path.exists():
    print("✗ 数据库不存在")
    exit(1)

con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

# 查询最近被UNKNOWN_FIELD拒绝的候选
# 尝试candidate_work_items表
query = """
SELECT candidate_id, payload_json, last_error_category, last_error, updated_at
FROM candidate_work_items
WHERE last_error_category = 'UNKNOWN_FIELD'
ORDER BY updated_at DESC
LIMIT 20
"""

try:
    rows = con.execute(query).fetchall()
    print(f"\n✓ 找到 {len(rows)} 个UNKNOWN_FIELD被拒候选 (candidate_work_items)\n")
except:
    # 如果没有，尝试candidate_outcomes
    query = """
    SELECT candidate_id, expression, error_category, error_message, observed_at
    FROM candidate_outcomes
    WHERE error_category = 'UNKNOWN_FIELD'
    ORDER BY observed_at DESC
    LIMIT 20
    """
    rows = con.execute(query).fetchall()
    print(f"\n✓ 找到 {len(rows)} 个UNKNOWN_FIELD被拒候选 (candidate_outcomes)\n")

if not rows:
    print("无UNKNOWN_FIELD案例，检查其他拒绝原因...")
    # 检查candidate_work_items
    query2 = """
    SELECT last_error_category, COUNT(*) as cnt
    FROM candidate_work_items
    WHERE state IN ('REJECTED', 'FAILED')
    GROUP BY last_error_category
    ORDER BY cnt DESC
    LIMIT 10
    """
    print("\n候选拒绝原因 (candidate_work_items):")
    for row in con.execute(query2):
        print(f"  {row['last_error_category']}: {row['cnt']}")

    # 检查candidate_outcomes
    query3 = """
    SELECT error_category, COUNT(*) as cnt
    FROM candidate_outcomes
    WHERE outcome IN ('REJECTED', 'FAILED')
    GROUP BY error_category
    ORDER BY cnt DESC
    LIMIT 10
    """
    print("\n候选拒绝原因 (candidate_outcomes):")
    for row in con.execute(query3):
        print(f"  {row['error_category']}: {row['cnt']}")
    exit(0)

# 分析未知字段
import re
field_pattern = re.compile(r'\b([a-z_][a-z0-9_]*)\b')

unknown_fields_found = {}
for row in rows:
    # 尝试从不同字段获取表达式和错误信息
    expr = ""
    detail = ""

    # candidate_work_items表
    if "payload_json" in row.keys():
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        expr = payload.get("expression", "")
        detail = row["last_error"] or ""
    # candidate_outcomes表
    elif "expression" in row.keys():
        expr = row["expression"] or ""
        detail = row["error_message"] or ""

    # 从error_detail中提取字段（通常格式为"unknown field: xxx"）
    if "field" in detail.lower():
        matches = re.findall(r'[:\s]([a-z_][a-z0-9_]+)', detail)
        for m in matches:
            if m not in known_fields:
                unknown_fields_found[m] = unknown_fields_found.get(m, 0) + 1

    # 同时从expression中提取所有可能的字段
    for match in field_pattern.findall(expr):
        if match not in known_fields and len(match) > 3:  # 排除短关键字
            if match not in ['rank', 'mean', 'std', 'sum', 'max', 'min', 'abs']:  # 排除算子名
                unknown_fields_found[match] = unknown_fields_found.get(m, 0) + 1

print("═" * 80)
print("未知字段统计（不在5697字段catalog中）:")
print("═" * 80)
for field, count in sorted(unknown_fields_found.items(), key=lambda x: -x[1])[:30]:
    print(f"  {field}: {count}次")

print(f"\n✓ 共发现 {len(unknown_fields_found)} 个未知字段")

con.close()
