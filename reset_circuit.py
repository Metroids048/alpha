#!/usr/bin/env python3
"""重置熔断器为CLOSED（平台已手动打通）"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

now = datetime.now(timezone.utc).isoformat()

print("=== 重置熔断器状态 ===")
print("原因: 用户已手动打通平台认证（人脸扫描）")

c.execute("""
    UPDATE platform_access_state
    SET state='CLOSED',
        retry_after_until=NULL,
        recovery_attempts=0,
        last_successful_auth=?,
        reason='manual_auth_success',
        updated_at=?
    WHERE singleton=1
""", (now, now))

conn.commit()

print(f"✓ 已重置为 CLOSED 状态")
print(f"  last_successful_auth: {now}")
print(f"  reason: manual_auth_success")

c.execute("SELECT state, retry_after_until, reason FROM platform_access_state WHERE singleton=1")
state, until, reason = c.fetchone()
print(f"\n验证: state={state}, retry_after_until={until}, reason={reason}")

conn.close()
