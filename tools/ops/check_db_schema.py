#!/usr/bin/env python3
"""检查数据库schema和数据"""
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

print('=' * 60)
print('📊 数据库表结构和数据统计')
print('=' * 60)

tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()

for (table,) in tables:
    print(f'\n📁 {table}:')
    count = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f'   总记录数: {count}')

    if count > 0:
        schema = con.execute(f'PRAGMA table_info({table})').fetchall()
        cols = [col[1] for col in schema]
        print(f'   字段: {", ".join(cols[:8])}{"..." if len(cols) > 8 else ""}')

print('\n' + '=' * 60)
print('🔍 检查关键数据')
print('=' * 60)

# Expressions
expr_count = con.execute('SELECT COUNT(*) FROM expressions').fetchone()[0]
print(f'\n✅ Expressions 总数: {expr_count}')

if expr_count > 0:
    recent = con.execute('SELECT id, code FROM expressions ORDER BY id DESC LIMIT 3').fetchall()
    print('   最近3条:')
    for eid, code in recent:
        print(f'     {eid}: {code[:50]}...')

# Simulation runs
sim_count = con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
valid_count = con.execute('SELECT COUNT(*) FROM simulation_runs WHERE expression_id IS NOT NULL').fetchone()[0]
print(f'\n✅ Simulation runs: {sim_count} (有效: {valid_count})')

# Factory control
print('\n⚙️ Factory control:')
fc = con.execute('SELECT key, value FROM factory_control').fetchall()
for k, v in fc:
    print(f'   {k}: {v}')

con.close()
