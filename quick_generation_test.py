#!/usr/bin/env python3
"""快速测试：生成1个新候选并验证认证"""
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

print(f"✓ WQ_USERNAME: {os.getenv('WQ_USERNAME', 'NOT SET')}")
print(f"✓ WQ_EMAIL: {os.getenv('WQ_EMAIL', 'NOT SET')}")

import sqlite3
database = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"

# 1. 清理所有FAR_FAIL候选
print("\n=== 清理旧候选 ===")
conn = sqlite3.connect(str(database))
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM candidate_work_items WHERE state='FAR_FAIL'")
old_count = c.fetchone()[0]
print(f"清理前FAR_FAIL数量: {old_count}")

c.execute("DELETE FROM candidate_work_items WHERE state='FAR_FAIL'")
conn.commit()
print(f"已清理 {c.rowcount} 条FAR_FAIL记录")

# 2. 生成1个新候选
print("\n=== 生成新候选 ===")
from alpha_mining.generation.service import CandidateGenerationService

service = CandidateGenerationService(str(database))

# 强制生成一个新候选
result = service.generate(limit=1)

print(f"\n生成结果:")
print(f"  生成数量: {len(result.candidates)}")
print(f"  拒绝情况: {result.rejected_by_reason}")
print(f"  生成状态: {result.generation_state}")

if result.candidates:
    print(f"\n✓ 成功生成 {len(result.candidates)} 个候选")

    # 3. 立即运行准备工作流
    print("\n=== 运行准备工作流 ===")
    from alpha_mining.generation.workflow import prepare_candidates_for_simulation

    prep_result = prepare_candidates_for_simulation(str(database), limit=1)

    print(f"\n准备结果:")
    print(f"  准备状态: {prep_result}")

    # 4. 检查结果
    c.execute("""
        SELECT state, COUNT(*)
        FROM candidate_work_items
        GROUP BY state
    """)
    print(f"\n当前候选状态分布:")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")

    # 5. 检查最新的simulation_requests
    c.execute("""
        SELECT request_id, status, error_message
        FROM simulation_requests
        ORDER BY submitted_at DESC
        LIMIT 1
    """)
    sim_row = c.fetchone()
    if sim_row:
        print(f"\n最新仿真请求:")
        print(f"  ID: {sim_row[0][:16]}...")
        print(f"  状态: {sim_row[1]}")
        print(f"  错误: {sim_row[2] or 'None'}")

        if sim_row[2] and 'WQ_USERNAME' in sim_row[2]:
            print("\n❌ 认证问题仍存在！")
        else:
            print("\n✓ 认证问题已解决！")
    else:
        print("\n没有仿真请求记录")
else:
    print("\n❌ 生成失败")

conn.close()
