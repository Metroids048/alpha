#!/usr/bin/env python3
"""检查熔断器状态表"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 检查 platform_access_state 表 ===")
try:
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='platform_access_state'")
    if c.fetchone():
        print("✓ 表存在")
        c.execute("SELECT COUNT(*) FROM platform_access_state WHERE singleton=1")
        count = c.fetchone()[0]
        if count == 0:
            print("✗ 表为空（singleton=1 行不存在）")
        else:
            print(f"✓ 有 {count} 行")
            c.execute("SELECT state, opened_at, retry_after_until, reason FROM platform_access_state WHERE singleton=1")
            state, opened, until, reason = c.fetchone()
            print(f"  state: {state}")
            print(f"  opened_at: {opened}")
            print(f"  retry_after_until: {until}")
            print(f"  reason: {reason}")
    else:
        print("✗ 表不存在")
except Exception as e:
    print(f"✗ 查询失败: {e}")

conn.close()
