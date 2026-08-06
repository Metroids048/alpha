import sqlite3
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = c.fetchall()
print("=== 所有表 ===")
for table in tables:
    print(table[0])
conn.close()
