#!/usr/bin/env python3
import sqlite3
import json

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
c = conn.cursor()

# 先检查表结构
print('=== candidate_work_events 表结构 ===')
c.execute("PRAGMA table_info(candidate_work_events)")
for row in c.fetchall():
    print(f'{row[1]} ({row[2]})')

print('\n=== 最近8条事件 ===')
c.execute('''
    SELECT candidate_id, event_type, details_json
    FROM candidate_work_events
    WHERE event_type IN ('SIMULATION_FAILED', 'SIMULATION_UNCERTAIN')
    ORDER BY event_at DESC
    LIMIT 8
''')
for row in c.fetchall():
    print(f'ID: {row[0][:16]}...')
    print(f'  Type: {row[1]}')
    if row[2]:
        details = json.loads(row[2])
        print(f'  Error Category: {details.get("error_category", "N/A")}')
        print(f'  Error: {str(details.get("error", "N/A"))[:200]}')
    print()

conn.close()
