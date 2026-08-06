import sqlite3
import json
from collections import Counter, defaultdict

# 连接canonical数据库
conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
cursor = conn.cursor()

print("=== 阶段2: 种子去重分析 ===\n")

# 1. 分析candidate_work_items中的拓扑重复
print("1️⃣ 分析种子拓扑重复:")
cursor.execute("""
    SELECT
        operator_topology,
        COUNT(*) as count
    FROM candidate_work_items
    WHERE operator_topology IS NOT NULL AND operator_topology != ''
    GROUP BY operator_topology
    HAVING count > 1
    ORDER BY count DESC
    LIMIT 10
""")
topology_dupes = cursor.fetchall()

if topology_dupes:
    print(f"\n   发现 {len(topology_dupes)} 种重复拓扑:")
    for topo, count in topology_dupes[:5]:
        print(f"   - {topo[:60]}... : {count}次")
else:
    print("   ✅ 无拓扑重复")

# 2. 分析parent_template分布（了解当前种子来源）
print("\n2️⃣ 当前种子来源分布:")
cursor.execute("""
    SELECT
        parent_template,
        COUNT(*) as count
    FROM candidate_work_items
    WHERE parent_template IS NOT NULL AND parent_template != ''
    GROUP BY parent_template
    ORDER BY count DESC
    LIMIT 5
""")
parent_templates = cursor.fetchall()

if parent_templates:
    print(f"\n   前5个种子模板:")
    for template, count in parent_templates:
        print(f"   - {template[:60]}... : {count}个候选")
else:
    print("   ⚠️ 无parent_template信息")

# 3. 分析算子家族分布
print("\n3️⃣ 算子家族分布:")
cursor.execute("""
    SELECT
        operator_topology,
        COUNT(*) as count
    FROM candidate_work_items
    GROUP BY operator_topology
    ORDER BY count DESC
    LIMIT 10
""")
operator_families = cursor.fetchall()

# 提取主算子
from collections import Counter
main_operators = []
for topo, count in operator_families:
    if topo:
        # 提取第一个算子（通常是主算子）
        ops = topo.split('|')
        if ops:
            main_operators.append((ops[0], count))

if main_operators:
    op_counter = Counter()
    for op, count in main_operators:
        op_counter[op] += count

    print(f"\n   主算子使用频率:")
    for op, count in op_counter.most_common(8):
        print(f"   - {op}: {count}次 ({count/sum(op_counter.values())*100:.1f}%)")
else:
    print("   ⚠️ 无算子信息")

# 4. 检查v50配置中的种子池大小
print("\n4️⃣ V50种子池配置:")
print("   当前: seed_pool_size未知（需检查生成配置）")
print("   建议: 从80扩展到150-200（增加多样性）")

# 5. 生成扩展建议
print("\n5️⃣ 种子扩展策略:")

# 从当前operator_families推断缺失的算子家族
all_allowed_operators = [
    'ts_mean', 'ts_std_dev', 'ts_zscore', 'ts_delta',
    'ts_rank', 'rank', 'group_rank', 'group_mean',
    'group_neutralize', 'signed_power', 'ts_decay_linear',
    'ts_max', 'ts_min', 'ts_product'
]

used_ops = set(op for op, _ in main_operators)
missing_ops = set(all_allowed_operators) - used_ops

if missing_ops:
    print(f"\n   未使用的算子（可扩展）: {', '.join(list(missing_ops)[:8])}")
else:
    print(f"\n   ✅ 所有允许算子已使用")

# 6. 拓扑去重建议
print("\n6️⃣ 拓扑去重实施方案:")
print("   方案1: 在ExpressionFactory.generate()后添加structure_signature去重")
print("   方案2: 在入队前（CandidateCsvQueue.enqueue）增加拓扑检查")
print("   方案3: 扩展v50的history_skeletons包含operator_topology")

# 保存分析结果
analysis = {
    'topology_duplicates': len(topology_dupes),
    'top_duplicate_count': topology_dupes[0][1] if topology_dupes else 0,
    'unique_parent_templates': len(parent_templates),
    'operator_diversity': len(used_ops),
    'missing_operators': list(missing_ops)[:8],
    'recommendations': [
        'expand_seed_pool: 80 -> 150',
        'add_topology_dedup: pre-enqueue',
        'diversify_operators: add ' + ', '.join(list(missing_ops)[:3])
    ]
}

with open('stage2_seed_analysis.json', 'w') as f:
    json.dump(analysis, f, indent=2)

print("\n📊 分析结果已保存到: stage2_seed_analysis.json")

conn.close()

print("\n下一步: 修改V50配置扩展种子池 + 实现拓扑去重")
