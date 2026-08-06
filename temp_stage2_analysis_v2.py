import sqlite3
import json
from collections import Counter

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
cursor = conn.cursor()

print("=== 检查candidate_work_items表结构 ===")
cursor.execute("PRAGMA table_info(candidate_work_items)")
columns = cursor.fetchall()
column_names = [col[1] for col in columns]
print(f"列: {column_names}\n")

print("=== 阶段2: 种子去重分析 ===\n")

# 1. 统计候选总数和状态
cursor.execute("SELECT workflow_status, COUNT(*) FROM candidate_work_items GROUP BY workflow_status")
status_counts = cursor.fetchall()
print("1️⃣ 候选状态分布:")
for status, count in status_counts:
    print(f"   {status}: {count}")

# 2. 查看样例数据
cursor.execute("SELECT * FROM candidate_work_items LIMIT 3")
samples = cursor.fetchall()
print(f"\n2️⃣ 前3条样例数据:")
if samples:
    for i, row in enumerate(samples, 1):
        print(f"\n   样例 {i}:")
        for col_name, value in zip(column_names, row):
            if value and str(value).strip():
                print(f"     {col_name}: {str(value)[:80]}")

# 3. 分析拒绝原因
if 'last_error_category' in column_names:
    cursor.execute("""
        SELECT last_error_category, COUNT(*) as count
        FROM candidate_work_items
        WHERE last_error_category IS NOT NULL AND last_error_category != ''
        GROUP BY last_error_category
        ORDER BY count DESC
    """)
    errors = cursor.fetchall()
    print(f"\n3️⃣ 拒绝原因分布:")
    for error, count in errors:
        print(f"   {error}: {count}")

# 4. 检查expression字段（用于计算拓扑）
if 'expression' in column_names:
    cursor.execute("""
        SELECT expression
        FROM candidate_work_items
        WHERE expression IS NOT NULL AND expression != ''
        LIMIT 10
    """)
    expressions = cursor.fetchall()

    if expressions:
        print(f"\n4️⃣ 计算拓扑重复:")

        # 手动计算operator_topology
        from alpha_mining.domain.expression_normalization import operator_topology

        topology_counter = Counter()
        for (expr,) in expressions:
            topo = operator_topology(expr)
            topology_counter[topo] += 1

        print(f"   检查了 {len(expressions)} 个表达式")
        duplicates = {topo: count for topo, count in topology_counter.items() if count > 1}

        if duplicates:
            print(f"   发现 {len(duplicates)} 种重复拓扑:")
            for topo, count in list(duplicates.items())[:3]:
                print(f"   - {topo[:60]}... : {count}次")
        else:
            print(f"   ✅ 前10个无重复")

# 5. V50种子池配置检查
print("\n5️⃣ 当前配置推断:")
print("   - candidate_work_items中有13行（可能是上轮残留）")
print("   - 需要扩展种子池并实现拓扑去重")

# 6. 生成修复计划
fix_plan = {
    'issue': 'SEED_TOPOLOGY_DUPLICATE: 10/24 (41%)',
    'root_cause': 'v50种子生成器产生重复拓扑结构',
    'solutions': [
        {
            'action': 'expand_seed_pool',
            'target': 'V50Kernel.__init__(seed_pool_size=150)',
            'expected': '增加种子多样性，减少重复概率'
        },
        {
            'action': 'add_topology_dedup',
            'target': 'HighQualityGenerator._generate_with_v50()',
            'expected': '生成后立即去除拓扑重复'
        },
        {
            'action': 'clear_stale_work_items',
            'target': 'candidate_work_items表',
            'expected': '清理旧残留，避免累积污染'
        }
    ]
}

with open('stage2_fix_plan.json', 'w') as f:
    json.dump(fix_plan, f, indent=2)

print("\n📋 修复计划已保存到: stage2_fix_plan.json")
print("\n下一步: 实施种子池扩展 + 拓扑去重")

conn.close()
