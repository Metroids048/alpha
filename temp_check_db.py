import sqlite3

conn = sqlite3.connect('research_memory.sqlite')
cursor = conn.cursor()

# 查看所有表
print("=== 数据库表列表 ===")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
for table in tables:
    print(f"  - {table}")

# 检查是否有候选相关表
if 'candidates' in tables:
    cursor.execute("SELECT COUNT(*) FROM candidates WHERE last_error_category='LEGACY_CONTRACT_MISSING_EVIDENCE'")
    count = cursor.fetchone()[0]
    print(f"\n旧合同候选数量: {count}")
else:
    print("\n⚠️ candidates表不存在，数据可能存储在CSV中")

# 检查feedback表
if 'feedback' in tables:
    cursor.execute("SELECT COUNT(*) FROM feedback")
    feedback_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM feedback WHERE is_positive=1")
    positive_count = cursor.fetchone()[0]
    print(f"Feedback总数: {feedback_count}, Positive: {positive_count}")
else:
    print("\n⚠️ feedback表不存在")

# 检查factory_control表
if 'factory_control' in tables:
    cursor.execute("SELECT hard_stop, reason FROM factory_control WHERE singleton=1")
    row = cursor.fetchone()
    if row:
        print(f"\nFactory状态: hard_stop={row[0]}, reason={row[1]}")
else:
    print("\n⚠️ factory_control表不存在")

conn.close()
