import sqlite3
con = sqlite3.connect('research_memory.sqlite')

# 获取所有表
tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

print("数据库表及行数:")
print("=" * 60)
for table in tables:
    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"{table:40s} : {count:8d} 行")

print("\n" + "=" * 60)
print("检查非空表的内容...")

# 对于有数据的表，显示最近几条记录
for table in tables:
    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if count > 0:
        print(f"\n{table} (最近5条):")
        # 获取列名
        cursor = con.execute(f"SELECT * FROM {table} LIMIT 1")
        columns = [description[0] for description in cursor.description]
        print(f"  列: {', '.join(columns)}")

        # 显示最近几条
        rows = con.execute(f"SELECT * FROM {table} LIMIT 5").fetchall()
        for i, row in enumerate(rows, 1):
            print(f"  [{i}] {dict(zip(columns, row))}")

con.close()
