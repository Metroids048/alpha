import sqlite3
con = sqlite3.connect('research_memory.sqlite')
tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("数据库表:", tables)
for table in tables:
    print(f"\n{table} 表结构:")
    schema = con.execute(f"PRAGMA table_info({table})").fetchall()
    for col in schema:
        print(f"  {col[1]} ({col[2]})")
con.close()
