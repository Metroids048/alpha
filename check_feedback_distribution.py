#!/usr/bin/env python3
"""检查feedback表的真实内容分布"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")

with sqlite3.connect(database) as con:
    print("=== feedback表总览 ===")
    total = con.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    print(f"总记录数: {total}")

    print("\n=== outcome分布 ===")
    rows = con.execute("""
        SELECT outcome, COUNT(*) as cnt
        FROM feedback
        GROUP BY outcome
        ORDER BY cnt DESC
    """).fetchall()
    for outcome, cnt in rows:
        print(f"  {outcome}: {cnt}")

    print("\n=== family分布 ===")
    rows = con.execute("""
        SELECT family, COUNT(*) as cnt
        FROM feedback
        GROUP BY family
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    for family, cnt in rows:
        print(f"  {family}: {cnt}")

    print("\n=== grounded分布（有表达式的反馈）===")
    rows = con.execute("""
        SELECT
            CASE WHEN grounded=1 THEN 'grounded' ELSE 'not_grounded' END as g,
            COUNT(*) as cnt
        FROM feedback
        GROUP BY g
    """).fetchall()
    for g, cnt in rows:
        print(f"  {g}: {cnt}")

    print("\n=== positive反馈（PASS）===")
    positive = con.execute("""
        SELECT ref_id, expression, family
        FROM feedback
        WHERE outcome = 'PASS'
        LIMIT 5
    """).fetchall()
    print(f"数量: {len(positive)}")
    for ref_id, expr, fam in positive:
        print(f"  {ref_id} | {fam} | {expr[:60] if expr else '(无表达式)'}...")

    print("\n=== 最近的反馈记录（前10条）===")
    recent = con.execute("""
        SELECT ref_id, outcome, family, grounded, failure_types
        FROM feedback
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()
    for ref_id, outcome, fam, grounded, failures in recent:
        print(f"  {ref_id} | {outcome} | {fam} | grounded={grounded} | {failures}")
