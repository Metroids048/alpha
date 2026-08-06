#!/usr/bin/env python3
"""诊断为什么候选会直接FAR_FAIL"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# 加载 .env
env_file = _ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

import sqlite3
import json
database = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"

# 获取一个FAR_FAIL候选并分析
conn = sqlite3.connect(str(database))
c = conn.cursor()
c.execute('''
    SELECT candidate_id, payload_json, state, updated_at
    FROM candidate_work_items
    WHERE state = 'FAR_FAIL'
    ORDER BY updated_at DESC
    LIMIT 1
''')
row = c.fetchone()

if not row:
    print("没有FAR_FAIL候选")
    sys.exit(0)

candidate_id, payload_json, state, updated_at = row
payload = json.loads(payload_json)

print(f"=== 候选 {candidate_id[:16]}... ===")
print(f"状态: {state}")
print(f"更新时间: {updated_at}")
print(f"\n表达式: {payload.get('expression', 'N/A')[:100]}...")

# 检查identity
from alpha_mining.domain.expression_normalization import expression_identity

expr = payload.get('expression', '')
if expr:
    identity = expression_identity(expr)
    print(f"\n=== Identity 检查 ===")
    print(f"exact_hash: {identity.exact_hash[:16]}...")
    print(f"parameter_skeleton: {identity.parameter_skeleton}")
    print(f"field_skeleton: {identity.field_skeleton}")

    if not identity.parameter_skeleton or not identity.field_skeleton:
        print("\n❌ IDENTITY 验证失败！parameter_skeleton或field_skeleton为空")
        print("   这会导致claim被拒绝")
    else:
        print("\n✅ IDENTITY 验证通过")

# 检查是否有simulation_requests记录
c.execute('''
    SELECT COUNT(*) FROM simulation_requests
    WHERE alpha_id = ?
''', (candidate_id,))
count = c.fetchone()[0]
print(f"\n仿真请求记录数: {count}")

if count == 0:
    print("   ❌ 没有任何仿真请求记录，说明在claim阶段就失败了")

# 检查candidate_outcomes记录
c.execute('''
    SELECT outcome, error_category, error_message
    FROM candidate_outcomes
    WHERE candidate_id = ?
    ORDER BY observed_at DESC
    LIMIT 1
''', (candidate_id,))
fb_row = c.fetchone()
if fb_row:
    print(f"\n=== Outcome 记录 ===")
    print(f"outcome: {fb_row[0]}")
    print(f"error_category: {fb_row[1]}")
    print(f"error_message: {fb_row[2]}")
else:
    print("\n没有outcome记录")

# 检查是否有exact_hash冲突
if expr:
    identity = expression_identity(expr)
    c.execute('''
        SELECT COUNT(*) FROM expression_identities
        WHERE exact_hash = ?
    ''', (identity.exact_hash,))
    eid_count = c.fetchone()[0]

    c.execute('''
        SELECT COUNT(*) FROM factory_candidate_claims
        WHERE exact_hash = ?
    ''', (identity.exact_hash,))
    claim_count = c.fetchone()[0]

    print(f"\n=== 历史重复检查 ===")
    print(f"expression_identities表中重复: {eid_count}")
    print(f"factory_candidate_claims表中重复: {claim_count}")

    if eid_count > 0 or claim_count > 0:
        print("   ❌ exact_hash已存在！这会导致claim被拒绝（除非allow_existing_identity=True）")

conn.close()
