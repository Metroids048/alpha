#!/usr/bin/env python3
"""检查candidate_outcomes表的真实分布"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")

with sqlite3.connect(database) as con:
    print("=== candidate_outcomes 表总览 ===")
    total = con.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
    print(f"总记录数: {total}")

    print("\n=== outcome 分布 ===")
    rows = con.execute("""
        SELECT outcome, COUNT(*) as cnt
        FROM candidate_outcomes
        GROUP BY outcome
        ORDER BY cnt DESC
    """).fetchall()
    for outcome, cnt in rows:
        print(f"  {outcome}: {cnt}")

    print("\n=== strategy_family 分布（前10）===")
    rows = con.execute("""
        SELECT strategy_family, COUNT(*) as cnt
        FROM candidate_outcomes
        GROUP BY strategy_family
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    for family, cnt in rows:
        print(f"  {family}: {cnt}")

    print("\n=== PASS 反馈样本（前5条）===")
    positive = con.execute("""
        SELECT request_hash, strategy_family, sharpe, fitness, checks_json
        FROM candidate_outcomes
        WHERE outcome = 'PASS'
        ORDER BY observed_at DESC
        LIMIT 5
    """).fetchall()
    if positive:
        for req_hash, fam, sharpe, fitness, checks in positive:
            print(f"  {req_hash[:16]}... | {fam} | sharpe={sharpe:.3f} | fitness={fitness:.3f}")
    else:
        print("  ⚠️ 没有 PASS 记录")

    print("\n=== FAIL 反馈样本（前5条）===")
    negative = con.execute("""
        SELECT request_hash, strategy_family, error_category, error_message
        FROM candidate_outcomes
        WHERE outcome = 'FAIL'
        ORDER BY observed_at DESC
        LIMIT 5
    """).fetchall()
    if negative:
        for req_hash, fam, err_cat, err_msg in negative:
            msg_preview = (err_msg[:50] + '...') if len(err_msg) > 50 else err_msg
            print(f"  {req_hash[:16]}... | {fam} | {err_cat} | {msg_preview}")
    else:
        print("  ⚠️ 没有 FAIL 记录")

    print("\n=== 质量状态分布（quality_status）===")
    rows = con.execute("""
        SELECT quality_status, COUNT(*) as cnt
        FROM candidate_outcomes
        WHERE quality_status != ''
        GROUP BY quality_status
        ORDER BY cnt DESC
    """).fetchall()
    if rows:
        for status, cnt in rows:
            print(f"  {status}: {cnt}")
    else:
        print("  ⚠️ 没有质量状态记录")

    print("\n=== 数据集分布 ===")
    rows = con.execute("""
        SELECT dataset, COUNT(*) as cnt
        FROM candidate_outcomes
        GROUP BY dataset
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()
    for ds, cnt in rows:
        print(f"  {ds}: {cnt}")

    print("\n=== mechanism 分布（前10）===")
    rows = con.execute("""
        SELECT mechanism, COUNT(*) as cnt
        FROM candidate_outcomes
        GROUP BY mechanism
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    for mech, cnt in rows:
        print(f"  {mech}: {cnt}")
