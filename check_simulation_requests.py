#!/usr/bin/env python3
import sqlite3
import json

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
c = conn.cursor()

# 检查simulation_requests表结构
print('=== simulation_requests 表结构 ===')
c.execute("PRAGMA table_info(simulation_requests)")
cols = [row[1] for row in c.fetchall()]
print(', '.join(cols))

# 检查最近的仿真请求
print('\n=== 最近5条仿真请求 ===')
c.execute(f'''
    SELECT {', '.join(cols[:15])}
    FROM simulation_requests
    ORDER BY created_at DESC
    LIMIT 5
''')
for row in c.fetchall():
    print(f'\nHash: {row[0][:16] if row[0] else "N/A"}...')
    for i, col in enumerate(cols[:15]):
        if row[i] and col not in ['created_at', 'updated_at']:
            val = str(row[i])
            print(f'  {col}: {val[:100]}')

conn.close()
