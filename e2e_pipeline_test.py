#!/usr/bin/env python3
"""端到端管道验证：生成 → Simulate"""
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

print(f"WQ_USERNAME: {os.getenv('WQ_USERNAME', 'NOT SET')}")

import sqlite3
import subprocess
import json

canonical_db = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(canonical_db))
c = conn.cursor()

# Step 1: 清理旧FAR_FAIL候选
print("\n=== Step 1: 清理旧 FAR_FAIL 候选 ===")
c.execute("SELECT COUNT(*) FROM candidate_work_items WHERE state='FAR_FAIL'")
old_far = c.fetchone()[0]
print(f"清理前: {old_far} 个 FAR_FAIL")
c.execute("DELETE FROM candidate_work_items WHERE state='FAR_FAIL'")
conn.commit()
print(f"已清理 {c.rowcount} 条")

# 重置所有PENDING_SIMULATION状态（让CSV驱动）
c.execute("SELECT COUNT(*) FROM candidate_work_items WHERE state='PENDING_SIMULATION'")
pending = c.fetchone()[0]
print(f"当前 PENDING_SIMULATION: {pending}")

conn.close()

# Step 2: 运行生成脚本（最多等120秒）
print("\n=== Step 2: 运行 生成Alpha.py --once ===")
result = subprocess.run(
    [sys.executable, "生成Alpha.py", "--once"],
    capture_output=True,
    text=True,
    timeout=180,
    cwd=str(_ROOT)
)

print(f"退出码: {result.returncode}")
# 提取关键行
for line in (result.stdout + result.stderr).splitlines():
    if any(k in line for k in ["cycle_id=", "enqueued=", "DEGRADED", "PORTFOLIO"]):
        print(f"  >> {line}")

# 查新生成的候选
conn = sqlite3.connect(str(canonical_db))
c = conn.cursor()
c.execute("SELECT state, COUNT(*) FROM candidate_work_items GROUP BY state")
state_dist = dict(c.fetchall())
print(f"生成后状态分布: {state_dist}")

pending_sim = state_dist.get("PENDING_SIMULATION", 0)
print(f"PENDING_SIMULATION: {pending_sim}")
conn.close()

if pending_sim == 0:
    print("\n⚠️  没有新的 PENDING_SIMULATION 候选，跳过 simulate 步骤")
    sys.exit(0)

# Step 3: 运行提交脚本（触发 simulate）
print(f"\n=== Step 3: 运行 提交Alpha.py --once (simulate {pending_sim} 个) ===")
result2 = subprocess.run(
    [sys.executable, "提交Alpha.py", "--once"],
    capture_output=True,
    text=True,
    timeout=300,
    cwd=str(_ROOT)
)

print(f"退出码: {result2.returncode}")
try:
    out = json.loads(result2.stdout.strip().splitlines()[-1])
    print(f"处理结果: processed={out.get('processed',0)}, simulated={out.get('simulated',0)}")
    print(f"状态分布: {out.get('states',{})}")
except:
    print("stdout:", result2.stdout[-500:] if result2.stdout else "(空)")
    print("stderr:", result2.stderr[-500:] if result2.stderr else "(空)")

# Step 4: 最终状态检查
print("\n=== Step 4: 最终状态检查 ===")
conn = sqlite3.connect(str(canonical_db))
c = conn.cursor()
c.execute("SELECT state, COUNT(*) FROM candidate_work_items GROUP BY state ORDER BY COUNT(*) DESC")
final_states = dict(c.fetchall())
print(f"最终状态: {final_states}")

# 看有没有通过的
passed = sum(final_states.get(s, 0) for s in ["NEAR_PASS", "READY_TO_SUBMIT", "PASS", "DESCRIPTION_VALIDATED"])
print(f"\n通过 / 可提交: {passed}")
if passed > 0:
    print("✅ 生成链路已打通！有候选通过质量检验。")
else:
    # 看失败原因
    c.execute("""SELECT candidate_id, last_error_category, last_error, updated_at
                 FROM candidate_work_items
                 WHERE state='FAR_FAIL'
                 ORDER BY updated_at DESC LIMIT 3""")
    for row in c.fetchall():
        print(f"FAR_FAIL: {row[0][:16]}... | {row[1]} | {str(row[2])[:120]}")

conn.close()
