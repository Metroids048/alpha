import sqlite3
from pathlib import Path

canonical_path = Path('数据/本地运行产物/数据库/research_memory.sqlite')
legacy_path = Path('research_memory.sqlite')

print("=== Canonical数据库 ===")
print(f"路径: {canonical_path}")
if canonical_path.exists():
    with sqlite3.connect(canonical_path) as con:
        watched = ['candidate_work_items', 'simulation_requests', 'candidate_outcomes', 'consultant_submit_queue']
        total = 0
        for table in watched:
            try:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count} 行")
                total += count
            except sqlite3.OperationalError:
                print(f"  {table}: 表不存在")
        print(f"  总计: {total} 行工作项")
else:
    print("  ❌ 文件不存在")

print("\n=== Legacy数据库 ===")
print(f"路径: {legacy_path}")
with sqlite3.connect(legacy_path) as con:
    watched = ['candidate_work_items', 'simulation_requests', 'candidate_outcomes', 'consultant_submit_queue']
    total = 0
    for table in watched:
        try:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count > 0:
                print(f"  {table}: {count} 行")
            total += count
        except sqlite3.OperationalError:
            pass
    print(f"  总计: {total} 行工作项")

print("\n=== 解决方案 ===")
print("需要迁移legacy的candidate_outcomes（反馈种子）到canonical")
print("然后清空legacy的工作项")

# 迁移反馈种子
if canonical_path.exists():
    print("\n执行迁移...")
    with sqlite3.connect(legacy_path) as legacy_con:
        legacy_cursor = legacy_con.cursor()
        legacy_cursor.execute("SELECT * FROM candidate_outcomes")
        rows = legacy_cursor.fetchall()

        if rows:
            # 获取列名
            legacy_cursor.execute("PRAGMA table_info(candidate_outcomes)")
            columns = [col[1] for col in legacy_cursor.fetchall()]

            print(f"  从legacy读取 {len(rows)} 行candidate_outcomes")

            with sqlite3.connect(canonical_path) as canonical_con:
                # 确保表结构存在
                from alpha_mining.generation.feedback import CandidateFeedbackStore
                CandidateFeedbackStore(canonical_path)

                # 插入数据
                placeholders = ','.join(['?' for _ in columns])
                insert_sql = f"INSERT OR REPLACE INTO candidate_outcomes ({','.join(columns)}) VALUES ({placeholders})"
                canonical_con.executemany(insert_sql, rows)
                canonical_con.commit()

                print(f"  ✅ 已迁移 {len(rows)} 行到canonical")

            # 清空legacy的candidate_outcomes
            legacy_con.execute("DELETE FROM candidate_outcomes")
            legacy_con.commit()
            print(f"  ✅ 已清空legacy的candidate_outcomes")
        else:
            print("  无数据需要迁移")

    # 验证
    print("\n=== 迁移后验证 ===")
    with sqlite3.connect(canonical_path) as con:
        count = con.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
        print(f"Canonical candidate_outcomes: {count} 行")

    with sqlite3.connect(legacy_path) as con:
        count = con.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
        print(f"Legacy candidate_outcomes: {count} 行")

    print("\n✅ 迁移完成，dual-ledger冲突应已解决")
else:
    print("❌ Canonical数据库不存在，需要先创建")
