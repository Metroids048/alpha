#!/usr/bin/env python3
"""重置一个FAR_FAIL候选并立即测试仿真"""
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

print("=== 环境变量检查 ===")
print(f"WQ_USERNAME: {os.getenv('WQ_USERNAME')}")
print(f"WQ_PASSWORD: {'SET' if os.getenv('WQ_PASSWORD') else 'NOT SET'}")

import sqlite3
database = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
conn = sqlite3.connect(str(database))
c = conn.cursor()

# 获取一个FAR_FAIL候选
c.execute('''
    SELECT candidate_id, payload_json
    FROM candidate_work_items
    WHERE state = 'FAR_FAIL'
    LIMIT 1
''')
row = c.fetchone()

if not row:
    print("没有FAR_FAIL候选")
    sys.exit(0)

candidate_id = row[0]
print(f"\n=== 重置候选 {candidate_id[:16]}... ===")

# 重置为PENDING_SIMULATION（这是正确的初始状态）
c.execute('''
    UPDATE candidate_work_items
    SET state = 'PENDING_SIMULATION', updated_at = CURRENT_TIMESTAMP
    WHERE candidate_id = ?
''', (candidate_id,))

# 删除旧的失败仿真请求（这样会重新尝试）
c.execute('''
    DELETE FROM simulation_requests
    WHERE alpha_id = ?
''', (candidate_id,))

conn.commit()
conn.close()

print("已重置为PENDING_SIMULATION并清除旧仿真记录")

# 立即测试仿真
print("\n=== 开始仿真测试 ===")
from alpha_mining.factory.operator_service import CandidateWorkflowService
from alpha_mining.storage.work_items import initialize_authoritative_database

initialize_authoritative_database(database, _ROOT / "research_memory.sqlite")
service = CandidateWorkflowService(database)

# 使用prepare_once来触发仿真
result = service.prepare_once(limit=1)
print(f"\n=== 仿真结果 ===")
print(f"Processed: {result.processed}")
print(f"Simulated: {result.simulated}")
print(f"States: {result.states}")

# 检查仿真请求状态
conn = sqlite3.connect(str(database))
c = conn.cursor()
c.execute('''
    SELECT request_hash, status, last_error
    FROM simulation_requests
    WHERE alpha_id = ?
    ORDER BY created_at DESC
    LIMIT 1
''', (candidate_id,))
row = c.fetchone()
if row:
    print(f"\n仿真请求详情:")
    print(f"  Hash: {row[0][:16]}...")
    print(f"  Status: {row[1]}")
    print(f"  Error: {row[2] or 'N/A'}")
else:
    print("\n没有找到仿真请求记录")

# 检查候选最终状态
c.execute('''
    SELECT state, updated_at
    FROM candidate_work_items
    WHERE candidate_id = ?
''', (candidate_id,))
row = c.fetchone()
if row:
    print(f"\n候选最终状态: {row[0]}")
    print(f"更新时间: {row[1]}")

conn.close()
