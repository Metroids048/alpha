#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

print("\n📊 Tables:")
tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    print(f"  - {t[0]}")

print("\n🔍 Recent simulation_runs:")
rows = con.execute('''
    SELECT expression_id, status, sharpe, fitness, turnover, fail_reason
    FROM simulation_runs
    ORDER BY id DESC
    LIMIT 20
''').fetchall()

for r in rows:
    expr_id = r[0] or 'None'
    print(f"  expr_{expr_id[:20]} | {r[1]} | sharpe={r[2]} fit={r[3]} turn={r[4]}")
    if r[5]:
        print(f"    ❌ {r[5]}")
