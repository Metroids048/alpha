#!/usr/bin/env python3
"""清理 expression_id 为 NULL 的 simulation_runs"""
import sqlite3

con = sqlite3.connect('alpha_state.sqlite3')

# 检查有多少条
null_count = con.execute("SELECT COUNT(*) FROM simulation_runs WHERE expression_id IS NULL").fetchone()[0]
print(f"🔍 发现 {null_count} 条 expression_id 为 NULL 的记录")

if null_count > 0:
    # 删除它们
    con.execute("DELETE FROM simulation_runs WHERE expression_id IS NULL")
    con.commit()
    print(f"✅ 已删除 {null_count} 条无效记录")

# 验证
remaining = con.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0]
print(f"📊 剩余 simulation_runs: {remaining}")

con.close()
print("\n✅ 清理完成，现在重启 pipeline 将生成正确的记录")
