#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
c = conn.cursor()

# 检查所有表
print('=== 数据库表 ===')
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
for row in c.fetchall():
    print(row[0])

# 检查队列状态
print('\n=== 队列候选状态 ===')
c.execute('SELECT state, COUNT(*) FROM candidate_work_items GROUP BY state ORDER BY COUNT(*) DESC')
for row in c.fetchall():
    print(f'{row[0]}: {row[1]}')

# 检查最近5条候选
print('\n=== 最近5条候选 ===')
c.execute('SELECT candidate_id, state, created_at FROM candidate_work_items ORDER BY created_at DESC LIMIT 5')
for row in c.fetchall():
    print(f'{row[0][:16]}... | {row[1]} | {row[2][:19]}')

conn.close()
