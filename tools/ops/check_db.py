import sqlite3

db = sqlite3.connect('alpha_state.sqlite3')
cursor = db.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
print('Available tables:')
for (table,) in tables:
    print(f'  - {table}')
    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
    count = cursor.fetchone()[0]
    print(f'    Records: {count}')

db.close()
