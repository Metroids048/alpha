#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')
cursor = con.cursor()

cursor.execute('SELECT COUNT(*) FROM data_mappings')
print(f'✅ Data mappings: {cursor.fetchone()[0]}')

cursor.execute('SELECT COUNT(*) FROM hypotheses WHERE status="active"')
print(f'✅ Active hypotheses: {cursor.fetchone()[0]}')

cursor.execute('SELECT dataset_id, data_field FROM data_mappings LIMIT 5')
print('\n📋 Sample mappings:')
for row in cursor.fetchall():
    print(f'  {row[0]}.{row[1]}')

con.close()
