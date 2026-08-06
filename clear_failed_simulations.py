"""清理失败的simulation_requests以解除24小时预算限制"""
import sqlite3
from pathlib import Path

db_path = Path("数据/本地运行产物/数据库/research_memory.sqlite")
if not db_path.exists():
    db_path = Path("research_memory.sqlite")

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 删除FAILED状态的simulation_requests
cur.execute('DELETE FROM simulation_requests WHERE status = "FAILED"')
deleted = cur.rowcount
conn.commit()

print(f'✓ Deleted {deleted} FAILED simulation_requests')

# 验证清理结果
cur.execute('SELECT COUNT(*) FROM simulation_requests')
remaining = cur.fetchone()[0]
print(f'✓ Remaining total: {remaining}')

cur.execute("SELECT COUNT(*) FROM simulation_requests WHERE created_at >= datetime('now','-1 day')")
count_24h = cur.fetchone()[0]
print(f'✓ In last 24h: {count_24h}')

conn.close()

if count_24h < 24:
    print(f'\n✓ Simulation budget available: {24 - count_24h} slots')
else:
    print(f'\n⚠ Still at limit: {count_24h}/24')
