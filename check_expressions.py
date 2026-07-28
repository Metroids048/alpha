#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

print("\n📝 Recent expressions:")
rows = con.execute('''
    SELECT expression_id, expression_text, hypothesis_id, generation_strategy, created_at
    FROM expressions
    ORDER BY created_at DESC
    LIMIT 10
''').fetchall()

if not rows:
    print("  ⚠️ No expressions found!")
else:
    for r in rows:
        expr_id = r[0][:30] if r[0] else 'None'
        code = r[1][:60] if r[1] else 'None'
        hyp = r[2][:20] if r[2] else 'None'
        print(f"  {expr_id}")
        print(f"    hyp: {hyp} | {r[3]} | {r[4]}")
        print(f"    code: {code}")

print(f"\n📊 Total expressions: {con.execute('SELECT COUNT(*) FROM expressions').fetchone()[0]}")
print(f"📊 Total simulation_runs: {con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]}")
print(f"📊 Total hypotheses: {con.execute('SELECT COUNT(*) FROM hypotheses WHERE status=\"ACTIVE\"').fetchone()[0]}")
