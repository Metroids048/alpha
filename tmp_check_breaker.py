#!/usr/bin/env python3
"""Quick breaker status check without running the full reconciliation."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path("research_memory.sqlite")
conn = sqlite3.connect(DB)

row = conn.execute(
    "SELECT state, opened_at, retry_after_until, recovery_attempts, reason "
    "FROM platform_access_state WHERE singleton=1"
).fetchone()

if not row:
    print("NO breaker state found")
    raise SystemExit(1)

state, opened_at, until, attempts, reason = row
print(f"state: {state}")
print(f"reason: {reason}")
print(f"opened_at: {opened_at}")
print(f"retry_after_until: {until}")
print(f"recovery_attempts: {attempts}")

if until:
    u = datetime.fromisoformat(until.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    remaining = (u - now).total_seconds()
    print(f"remaining_seconds: {remaining:.0f}")
    print(f"remaining_minutes: {remaining / 60:.1f}")
    if remaining > 0:
        print(f"\n窗口到 {until} 才过期，还剩 {remaining/60:.1f} 分钟。")
    else:
        print(f"\n窗口已过期（{-remaining:.0f}秒前），可以发 recovery probe。")
