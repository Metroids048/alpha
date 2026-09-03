#!/usr/bin/env python3
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

print("Status counts:")
active_count = con.execute(
    "SELECT COUNT(*) FROM hypotheses WHERE status='ACTIVE'"
).fetchone()[0]
lowercase_active_count = con.execute(
    "SELECT COUNT(*) FROM hypotheses WHERE status='active'"
).fetchone()[0]
print(f"  ACTIVE: {active_count}")
print(f"  active: {lowercase_active_count}")

print("\nAll distinct statuses:")
statuses = con.execute('SELECT DISTINCT status FROM hypotheses').fetchall()
for s in statuses:
    count = con.execute(f'SELECT COUNT(*) FROM hypotheses WHERE status=?', (s[0],)).fetchone()[0]
    print(f"  '{s[0]}': {count}")
