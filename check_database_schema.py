#!/usr/bin/env python3
"""检查数据库中有哪些表"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")

with sqlite3.connect(database) as con:
    print("=== 数据库中的所有表 ===")
    rows = con.execute("""
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table', 'view')
        ORDER BY name
    """).fetchall()
    for name, typ in rows:
        print(f"  {typ}: {name}")

    print("\n=== 检查是否有feedback相关的表 ===")
    feedback_tables = [name for name, _ in rows if 'feedback' in name.lower()]
    if feedback_tables:
        print(f"找到: {feedback_tables}")
        for table in feedback_tables:
            cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {cnt} 行")
    else:
        print("⚠️ 没有找到任何feedback相关的表！")

    print("\n=== 检查observations表（可能是feedback的来源）===")
    obs_tables = [name for name, _ in rows if 'observ' in name.lower()]
    if obs_tables:
        print(f"找到: {obs_tables}")
        for table in obs_tables:
            cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {cnt} 行")
            # 查看结构
            schema = con.execute(f"PRAGMA table_info({table})").fetchall()
            print(f"  结构: {[col[1] for col in schema]}")
