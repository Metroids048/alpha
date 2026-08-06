#!/usr/bin/env python3
"""快速测试认证和仿真是否正常工作"""
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

# 测试一个简单的候选仿真
from alpha_mining.factory.operator_service import CandidateWorkflowService
from alpha_mining.storage.work_items import initialize_authoritative_database

database = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
initialize_authoritative_database(database, _ROOT / "research_memory.sqlite")

service = CandidateWorkflowService(database)

# 获取一个PENDING候选
pending = service.list_items(states=["PENDING"], limit=1)

if pending:
    item = pending[0]
    print(f"\n=== 测试候选 {item.candidate_id[:16]}... ===")
    print(f"Expression: {item.expression[:60]}...")

    # 尝试仿真
    result = service.process_batch([item.candidate_id], dry_run=False)
    print(f"\n=== 仿真结果 ===")
    print(f"Simulated: {result.get('simulated', 0)}")
    print(f"States: {result.get('states', {})}")
else:
    print("\n没有PENDING候选可测试")

    # 检查最近的仿真请求
    import sqlite3
    conn = sqlite3.connect(str(database))
    c = conn.cursor()
    c.execute('''
        SELECT request_hash, status, last_error
        FROM simulation_requests
        ORDER BY created_at DESC
        LIMIT 1
    ''')
    row = c.fetchone()
    if row:
        print(f"\n最近的仿真请求:")
        print(f"  Hash: {row[0][:16]}...")
        print(f"  Status: {row[1]}")
        print(f"  Error: {row[2] or 'N/A'}")
    conn.close()
