import sqlite3

db = sqlite3.connect('alpha_state.sqlite3')
cursor = db.cursor()

# 列出所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]
print(f'Total tables: {len(tables)}')
print('\nAll tables:')
for table in tables:
    print(f'  - {table}')

# 检查是否有 hypotheses 相关的表
hyp_tables = [t for t in tables if 'hypo' in t.lower() or 'topic' in t.lower() or 'mapping' in t.lower()]
print(f'\nHypothesis/topic/mapping related tables: {hyp_tables}')

db.close()
