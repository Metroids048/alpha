import sqlite3
import json
from collections import Counter

conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')
cursor = conn.cursor()

print("=== 阶段2: 种子去重诊断 ===\n")

# 1. 统计候选状态
cursor.execute("SELECT state, COUNT(*) FROM candidate_work_items GROUP BY state")
status_counts = cursor.fetchall()
print("1️⃣ 候选状态分布:")
for status, count in status_counts:
    print(f"   {status}: {count}")

# 2. 分析拒绝原因
cursor.execute("""
    SELECT last_error_category, COUNT(*) as count
    FROM candidate_work_items
    WHERE last_error_category IS NOT NULL AND last_error_category != ''
    GROUP BY last_error_category
    ORDER BY count DESC
""")
errors = cursor.fetchall()
print(f"\n2️⃣ 拒绝原因分布:")
total_rejected = sum(count for _, count in errors)
for error, count in errors:
    pct = count / total_rejected * 100 if total_rejected else 0
    print(f"   {error}: {count} ({pct:.1f}%)")

# 3. 提取payload_json分析拓扑
cursor.execute("""
    SELECT payload_json
    FROM candidate_work_items
    WHERE payload_json IS NOT NULL
    LIMIT 13
""")
payloads = cursor.fetchall()

print(f"\n3️⃣ 拓扑重复分析（样本{len(payloads)}个）:")

if payloads:
    from alpha_mining.domain.expression_normalization import operator_topology

    topology_counter = Counter()
    expressions = []

    for (payload_str,) in payloads:
        try:
            payload = json.loads(payload_str)
            expr = payload.get('expression', '')
            if expr:
                expressions.append(expr)
                topo = operator_topology(expr)
                topology_counter[topo] += 1
        except:
            pass

    print(f"   成功解析 {len(expressions)} 个表达式")

    duplicates = {topo: count for topo, count in topology_counter.items() if count > 1}

    if duplicates:
        print(f"   ⚠️ 发现 {len(duplicates)} 种重复拓扑:")
        for topo, count in list(duplicates.items())[:5]:
            print(f"     - {topo[:70]}... : {count}次")
    else:
        print(f"   ✅ 无拓扑重复（样本内）")

    # 统计算子使用
    operator_counter = Counter()
    for expr in expressions:
        # 提取主算子（简单方法：找第一个括号前的词）
        import re
        ops = re.findall(r'\b([a-z_]+)\s*\(', expr)
        operator_counter.update(ops)

    print(f"\n   算子使用频率（Top 8）:")
    for op, count in operator_counter.most_common(8):
        print(f"     - {op}: {count}次")

# 4. 清理旧work_items（避免累积污染）
print(f"\n4️⃣ 清理旧work_items:")
cursor.execute("SELECT COUNT(*) FROM candidate_work_items")
old_count = cursor.fetchone()[0]

if old_count > 0:
    print(f"   当前有 {old_count} 行旧候选")
    print(f"   建议: 清空candidate_work_items（这些是上轮残留）")

    # 清空（保守：只清理REJECTED的）
    cursor.execute("""
        DELETE FROM candidate_work_items
        WHERE state IN ('REJECTED', 'FAILED')
    """)
    conn.commit()

    deleted = old_count - cursor.execute("SELECT COUNT(*) FROM candidate_work_items").fetchone()[0]
    print(f"   ✅ 已清理 {deleted} 行REJECTED/FAILED候选")

# 5. 生成阶段2执行计划
fix_actions = {
    'timestamp': '2026-08-06T14:00:00Z',
    'stage': 'stage2_seed_dedup',
    'actions': [
        {
            'step': '2.1',
            'action': 'expand_v50_seed_pool',
            'file': 'alpha_mining/generation/production.py',
            'change': 'V50Kernel(seed_pool_size=150)',  # 从默认80扩展
            'expected': '种子多样性增加，重复概率降低'
        },
        {
            'step': '2.2',
            'action': 'add_topology_dedup',
            'file': 'alpha_mining/generation/high_quality.py',
            'change': '在_generate_with_v50()添加structure_signature去重',
            'expected': 'SEED_TOPOLOGY_DUPLICATE: 10 -> <3'
        },
        {
            'step': '2.3',
            'action': 'run_generation_test',
            'command': 'python 生成Alpha.py --once',
            'expected': 'enqueued > 0, rejected < 15'
        }
    ]
}

with open('stage2_execution_plan.json', 'w') as f:
    json.dump(fix_actions, f, indent=2)

print(f"\n📋 阶段2执行计划已保存到: stage2_execution_plan.json")
print(f"\n下一步: 修改代码实施种子池扩展 + 拓扑去重")

conn.close()
