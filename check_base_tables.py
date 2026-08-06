#!/usr/bin/env python3
"""检查基础表数据"""
import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

tables_to_check = [
    ('hypotheses', 'hypothesis_id', 'status'),
    ('research_topics', 'topic_id', 'active'),
    ('data_mappings', 'hypothesis_id', 'data_field')
]

for table, id_col, status_col in tables_to_check:
    c.execute(f"SELECT COUNT(*) FROM {table}")
    total = c.fetchone()[0]
    print(f"\n{table}: {total} 条记录")

    if total > 0:
        # 检查active/status字段分布
        try:
            c.execute(f"SELECT {status_col}, COUNT(*) FROM {table} GROUP BY {status_col}")
            print(f"  {status_col} 分布:")
            for row in c.fetchall():
                print(f"    {row[0]}: {row[1]}")
        except:
            pass

        # 显示几条样例
        c.execute(f"SELECT * FROM {table} LIMIT 2")
        print(f"  样例:")
        for row in c.fetchall():
            print(f"    {row[:5]}...")  # 只显示前5列

# 测试完整的查询
print("\n" + "="*50)
print("测试完整的research_specs查询:")
c.execute("""
    SELECT h.hypothesis_id, t.topic_id, t.category,
           h.mechanism, h.horizon, m.data_field, m.dataset_id
    FROM hypotheses h
    JOIN research_topics t ON t.topic_id=h.topic_id
    JOIN data_mappings m ON m.hypothesis_id=h.hypothesis_id
    WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1
    LIMIT 3
""")
specs = c.fetchall()
print(f"查询结果数量: {len(specs)}")
for spec in specs:
    print(f"  {spec}")

conn.close()
