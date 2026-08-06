import sqlite3
import pandas as pd

# 检查数据库
conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
cur = conn.cursor()

# 查看所有表
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cur.fetchall()]
print(f"数据库表: {tables}")

# 检查CSV
df = pd.read_csv('待提交Alpha列表.csv')
print(f"\nCSV总行数: {len(df)}")
print(f"queue_status分布:")
print(df['queue_status'].value_counts())

# 检查最近的状态
print(f"\n最近10条记录的状态:")
print(df[['expression', 'queue_status', 'local_quality_score']].tail(10).to_string())
