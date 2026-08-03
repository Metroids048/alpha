#!/usr/bin/env python3
"""诊断 pipeline 为什么不生成候选"""
import sqlite3
import sys

con = sqlite3.connect('alpha_state.sqlite3')

print("=" * 60)
print("🔍 Pipeline 诊断报告")
print("=" * 60)

# 1. Hypotheses
active_hyp = con.execute("SELECT COUNT(*) FROM hypotheses WHERE status='active'").fetchone()[0]
print(f"\n✅ Active hypotheses: {active_hyp}")

if active_hyp > 0:
    print("   Sample:")
    samples = con.execute("SELECT hypothesis_id, statement_cn FROM hypotheses WHERE status='active' LIMIT 3").fetchall()
    for h in samples:
        stmt = h[1][:40] if h[1] else 'N/A'
        print(f"     - {h[0][:40]}: {stmt}")

# 2. Data mappings
mappings = con.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0]
print(f"\n✅ Data mappings: {mappings}")

if mappings > 0:
    print("   Sample:")
    samples = con.execute("SELECT dataset_id, data_field FROM data_mappings LIMIT 5").fetchall()
    for m in samples:
        print(f"     - {m[0]}.{m[1]}")

# 3. Topics
topics = con.execute("SELECT COUNT(*) FROM research_topics").fetchone()[0]
print(f"\n✅ Research topics: {topics}")

# 4. Expressions
total_expr = con.execute("SELECT COUNT(*) FROM expressions").fetchone()[0]
print(f"\n📝 Total expressions generated: {total_expr}")

if total_expr > 0:
    recent = con.execute("""
        SELECT expression_id, hypothesis_id, created_at
        FROM expressions
        ORDER BY created_at DESC
        LIMIT 3
    """).fetchall()
    print("   Recent:")
    for e in recent:
        print(f"     - {e[0][:30]} | hyp={e[1][:30]} | {e[2]}")

# 5. Simulation runs
sims = con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0]
print(f"\n📊 Total simulation_runs: {sims}")

if sims > 0:
    recent = con.execute("""
        SELECT expression_id, status, sharpe, fitness, fail_reason
        FROM simulation_runs
        ORDER BY id DESC
        LIMIT 5
    """).fetchall()
    print("   Recent:")
    for s in recent:
        expr = s[0][:30] if s[0] else 'None'
        print(f"     - expr={expr} | {s[1]} | sharpe={s[2]} fit={s[3]}")
        if s[4] and s[4] != 'COMPLETE':
            print(f"       fail: {s[4]}")

# 6. 检查是否有阻塞原因
print("\n🚦 Checking for blockers:")

# 检查 simulation_runs 表中是否有挂起的请求
pending = con.execute("SELECT COUNT(*) FROM simulation_runs WHERE status='pending'").fetchone()[0]
if pending > 0:
    print(f"   ⚠️ {pending} pending simulations")

# 检查 expression_id 为空的情况
null_expr = con.execute("SELECT COUNT(*) FROM simulation_runs WHERE expression_id IS NULL").fetchone()[0]
if null_expr > 0:
    print(f"   ❌ {null_expr} simulation_runs have NULL expression_id!")

print("\n" + "=" * 60)
print("🎯 Summary:")
print("=" * 60)

if active_hyp == 0:
    print("❌ NO active hypotheses - pipeline cannot generate!")
    sys.exit(1)

if mappings == 0:
    print("❌ NO active data_mappings - generator has no fields!")
    sys.exit(1)

if total_expr == 0:
    print("⚠️ No expressions generated yet - first cycle hasn't run")
elif sims == 0:
    print("⚠️ Expressions generated but no simulations - simulation broken?")
else:
    print(f"✅ Pipeline has generated {total_expr} expressions and run {sims} simulations")

print("\nReady to generate? Check the next cycle output.")
