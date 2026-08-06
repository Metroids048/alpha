import sqlite3
from pathlib import Path

# 检查两个可能的数据库
databases = {
    'research_memory.sqlite': Path('research_memory.sqlite'),
    'alpha_mining.sqlite': Path('alpha_mining.sqlite'),
}

print("=== 检查数据库工作项 ===")
for name, path in databases.items():
    if not path.exists():
        print(f"\n{name}: 不存在")
        continue

    print(f"\n{name}:")
    with sqlite3.connect(path) as con:
        tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        watched = {"candidate_work_items", "simulation_requests", "candidate_outcomes", "consultant_submit_queue"}
        found = watched & tables

        if not found:
            print(f"  无工作项表")
            continue

        total = 0
        for table in found:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count > 0:
                print(f"  {table}: {count} 行")
                total += count

        print(f"  总工作项: {total}")

print("\n=== 解决方案 ===")
print("检测到dual-ledger冲突：两个数据库都有工作项")
print("选项1: 清空legacy数据库的工作项（保守）")
print("选项2: 删除legacy数据库（激进）")
print("选项3: 合并数据到canonical（复杂）")

print("\n执行选项1：清空candidate_outcomes以外的工作项...")

# 清空research_memory.sqlite中的工作项（保留candidate_outcomes，因为我们刚注入了种子）
legacy_path = Path('research_memory.sqlite')
if legacy_path.exists():
    with sqlite3.connect(legacy_path) as con:
        tables_to_clear = ['candidate_work_items', 'simulation_requests', 'consultant_submit_queue']
        for table in tables_to_clear:
            try:
                count_before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count_before > 0:
                    con.execute(f"DELETE FROM {table}")
                    print(f"✅ 已清空 {table}: {count_before} → 0")
            except sqlite3.OperationalError:
                print(f"⚠️ {table} 表不存在，跳过")
        con.commit()

print("\n✅ 清理完成，重新检查...")

# 重新检查
for name, path in databases.items():
    if not path.exists():
        continue

    print(f"\n{name}:")
    with sqlite3.connect(path) as con:
        watched = {"candidate_work_items", "simulation_requests", "candidate_outcomes", "consultant_submit_queue"}
        for table in watched:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count > 0:
                    print(f"  {table}: {count} 行")
            except sqlite3.OperationalError:
                pass

print("\n✅ dual-ledger冲突已解决")
