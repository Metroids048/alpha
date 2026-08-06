#!/usr/bin/env python3
"""检查candidate_outcomes与feedback的关联"""
import sqlite3
from pathlib import Path
import json

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== 1. candidate_outcomes中的PASS记录 ===")
c.execute("""
    SELECT candidate_id, outcome, sharpe, fitness, strategy_family, mechanism, observed_at
    FROM candidate_outcomes
    WHERE outcome = 'PASS'
    ORDER BY observed_at DESC
""")
pass_records = c.fetchall()
print(f"共{len(pass_records)}条PASS记录:\n")
for row in pass_records:
    cid, outcome, sharpe, fitness, family, mech, obs = row
    print(f"{obs}")
    print(f"  candidate_id: {cid}")
    print(f"  sharpe: {sharpe}, fitness: {fitness}")
    print(f"  family: {family}")
    print(f"  mechanism: {mech[:80]}...\n")

print("\n=== 2. 检查这些PASS的candidate_id是否在feedback表中 ===")
for row in pass_records:
    cid = row[0]
    c.execute("SELECT COUNT(*) FROM feedback WHERE candidate_id = ?", (cid,))
    cnt = c.fetchone()[0]
    print(f"candidate_id {cid[:40]}... : {'✓ 在feedback表中' if cnt > 0 else '✗ 不在feedback表中'}")

print("\n=== 3. feedback表总览 ===")
c.execute("SELECT COUNT(*) FROM feedback")
total_feedback = c.fetchone()[0]
print(f"feedback表总记录数: {total_feedback}")

if total_feedback > 0:
    c.execute("""
        SELECT candidate_id, label, sharpe_ratio, fitness_score, created_at
        FROM feedback
        ORDER BY created_at DESC
        LIMIT 5
    """)
    print("\n最近5条feedback:")
    for row in c.fetchall():
        cid, label, sharpe, fitness, created = row
        print(f"\n{created}")
        print(f"  candidate_id: {cid[:40] if cid else '(无)'}...")
        print(f"  label: {label}, sharpe: {sharpe}, fitness: {fitness}")

conn.close()
