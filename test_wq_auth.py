#!/usr/bin/env python
"""Test WQ authentication with current credentials."""
import os
import requests
from pathlib import Path

# Load .env
env_file = Path(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ[key.strip()] = val.strip()

username = os.environ.get("WQ_USERNAME", "").strip()
password = os.environ.get("WQ_PASSWORD", "")
proxy = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7892")

if not username or not password:
    print("❌ WQ_USERNAME or WQ_PASSWORD not set in .env")
    exit(1)

print(f"Testing authentication for {username[:3]}***@{username.split('@')[1] if '@' in username else '?'}")
print(f"Using proxy: {proxy}")

try:
    resp = requests.post(
        "https://api.worldquantbrain.com/authentication",
        auth=(username, password),
        proxies={"https": proxy},
        timeout=30,
    )
    print(f"\nHTTP {resp.status_code}")

    if resp.status_code == 200:
        print("✅ Authentication successful!")
        print(f"   Set-Cookie headers: {len([h for h in resp.headers if h.lower()=='set-cookie'])}")
    elif resp.status_code == 401:
        print("❌ Authentication failed (401 Unauthorized)")
        print("   Possible reasons:")
        print("   - Password expired/changed")
        print("   - Account locked due to too many failed attempts")
        print("   - WQ changed auth mechanism")
        print("\n   → Try logging in via browser first to verify account status")
    elif resp.status_code == 429:
        print("⚠️  Rate limited (429 Too Many Requests)")
        print("   → Wait 1-24 hours before retrying")
    else:
        print(f"⚠️  Unexpected response: {resp.status_code}")
        print(f"   Body: {resp.text[:200]}")
except Exception as e:
    print(f"❌ Request failed: {e}")
