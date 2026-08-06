import sqlite3
from pathlib import Path

# 检查所有可能的数据库路径
possible_dbs = [
    'research_memory.sqlite',
    'alpha_mining.sqlite',
    '.alpha_mining.sqlite',
    'alpha_state.sqlite',
]

print("=== 扫描所有数据库文件 ===")
for db_name in possible_dbs:
    path = Path(db_name)
    if not path.exists():
        continue

    print(f"\n📁 {db_name}:")
    try:
        with sqlite3.connect(path) as con:
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
            if total == 0:
                print(f"  (无工作项)")
    except Exception as e:
        print(f"  ❌ 无法读取: {e}")

# 检查待提交CSV（这个也可能被视为工作项）
csv_path = Path('待提交Alpha列表.csv')
if csv_path.exists():
    import pandas as pd
    df = pd.read_csv(csv_path)
    print(f"\n📄 待提交Alpha列表.csv: {len(df)} 行")
    print(f"   状态分布: {df['queue_status'].value_counts().to_dict()}")

print("\n=== 诊断 ===")
print("错误信息: canonical=13 legacy=1")
print("推测: canonical可能是某个隐藏数据库，legacy是research_memory.sqlite")
print("\n解决方案: 从CSV导入13行到research_memory，然后清空CSV")
