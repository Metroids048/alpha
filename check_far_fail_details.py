#!/usr/bin/env python3
import sqlite3
import json

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
c = conn.cursor()

# 检查FAR_FAIL候选的详细信息
print('=== FAR_FAIL候选详细信息 ===')
c.execute('''
    SELECT candidate_id, state, payload_json
    FROM candidate_work_items
    WHERE state = 'FAR_FAIL'
    ORDER BY created_at DESC
    LIMIT 3
''')
for row in c.fetchall():
    print(f'\nID: {row[0]}')
    print(f'State: {row[1]}')
    if row[2]:
        payload = json.loads(row[2])
        print(f'Expression: {str(payload.get("expression", ""))[:100]}...')
        print(f'Operator: {payload.get("operator_family", "N/A")}')
        print(f'Hypothesis: {payload.get("economic_hypothesis", "N/A")}')

# 检查最新的platform_request_events
print('\n=== 最近的平台请求事件 ===')
c.execute('''
    SELECT request_hash, status, error_category, error_message
    FROM platform_request_events
    ORDER BY created_at DESC
    LIMIT 5
''')
for row in c.fetchall():
    print(f'\nHash: {row[0][:16]}...')
    print(f'Status: {row[1]}')
    print(f'Error Category: {row[2] or "N/A"}')
    print(f'Error: {str(row[3] or "N/A")[:150]}')

conn.close()
