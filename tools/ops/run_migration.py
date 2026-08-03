from alpha_mining.storage.migrations import migrate

# 运行迁移
print("Running migrations on alpha_state.sqlite3...")
migrate('alpha_state.sqlite3')
print("✓ Migrations completed successfully!")

# 验证
import sqlite3
db = sqlite3.connect('alpha_state.sqlite3')
cursor = db.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
print(f"\n✓ Created {len(tables)} tables:")
for (table,) in tables[:10]:  # 显示前10个
    print(f"  - {table}")
if len(tables) > 10:
    print(f"  ... and {len(tables) - 10} more")
db.close()
