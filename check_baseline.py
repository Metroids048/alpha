#!/usr/bin/env python3
"""查询候选基线状态"""
import sqlite3

conn = sqlite3.connect('candidate.db')

print('[生成前基线]')
print('候选状态分布:')
for row in conn.execute('SELECT status, COUNT(*) FROM candidate GROUP BY status'):
    print(f'  {row[0]}: {row[1]}')

print()
print('最近拒绝原因TOP10:')
query = """
SELECT rejection_reason, COUNT(*) as cnt
FROM candidate
WHERE status='REJECTED' AND rejection_reason IS NOT NULL
GROUP BY rejection_reason
ORDER BY cnt DESC
LIMIT 10
"""
for row in conn.execute(query):
    print(f'  {row[0]}: {row[1]}')

conn.close()
