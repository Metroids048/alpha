#!/usr/bin/env python3
"""Dump cookies from browser profile and try to import."""
import sqlite3
import json
from pathlib import Path

cookie_db = Path(".wq_browser_profile/Default/Network/Cookies")
if not cookie_db.exists():
    print(f"Cookie DB not found: {cookie_db}")
    exit(1)

print(f"Reading cookies from: {cookie_db}")

# Read cookies from Chrome SQLite DB
conn = sqlite3.connect(str(cookie_db))
cursor = conn.cursor()

# Get schema
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cookies'")
schema = cursor.fetchone()
print(f"\nCookies table schema:\n{schema[0][:200]}...")

# Query cookies for worldquantbrain.com
cursor.execute("""
    SELECT name, encrypted_value, host_key, path, expires_utc
    FROM cookies
    WHERE host_key LIKE '%worldquantbrain%'
    ORDER BY creation_utc DESC
    LIMIT 10
""")

cookies = cursor.fetchall()
print(f"\nFound {len(cookies)} cookies for worldquantbrain.com:")
for name, enc_val, host, path, expires in cookies:
    print(f"  - {name}: host={host}, path={path}, encrypted={len(enc_val)} bytes, expires={expires}")
    if name in ('t', 'cf_clearance'):
        print(f"    *** AUTH COOKIE FOUND: {name} ***")

conn.close()

# Check if we have the required auth cookies
if any(c[0] == 't' for c in cookies):
    print("\n✓ Session token 't' cookie exists")
else:
    print("\n✗ Session token 't' cookie NOT FOUND")

if any(c[0] == 'cf_clearance' for c in cookies):
    print("✓ Cloudflare 'cf_clearance' cookie exists")
else:
    print("✗ Cloudflare 'cf_clearance' cookie NOT FOUND")
