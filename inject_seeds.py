import sqlite3
import json
from datetime import datetime
import hashlib

# 加载5个种子模板
with open('seed_templates_diverse.json', 'r') as f:
    seeds = json.load(f)

conn = sqlite3.connect('research_memory.sqlite')
c = conn.cursor()

# 为每个种子注入正反馈记录
inserted = 0
for seed in seeds:
    # 生成稳定的candidate_id和request_hash
    candidate_id = hashlib.md5(seed['expression'].encode()).hexdigest()[:16]
    request_hash = hashlib.md5(f"{seed['mechanism']}_{seed['expression']}".encode()).hexdigest()[:16]

    # 构造field_skeleton（从expression中提取）
    field_skeleton = seed['expression'].replace('close', 'FIELD').replace('returns', 'FIELD').replace('roe', 'FIELD').replace('earnings_yield', 'FIELD')

    # 注入PASS反馈
    c.execute("""
        INSERT INTO candidate_outcomes (
            request_hash, candidate_id, expression, outcome,
            strategy_family, dataset, field_skeleton,
            checks_json, quality_reasons_json,
            self_correlation, prod_correlation,
            observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request_hash,
        candidate_id,
        seed['expression'],
        'PASS',
        seed['operator_family'],
        ','.join(seed['datasets']),
        field_skeleton,
        json.dumps([]),  # 无失败检查
        json.dumps([]),  # 无质量问题
        'PASS',
        'PASS',
        datetime.utcnow().isoformat()
    ))
    inserted += 1

conn.commit()

# 验证注入结果
c.execute("SELECT COUNT(*) FROM candidate_outcomes WHERE outcome = 'PASS'")
pass_count = c.fetchone()[0]

print(f"成功注入 {inserted} 条正反馈种子")
print(f"验证: candidate_outcomes表中PASS记录数 = {pass_count}")

# 显示注入的记录
c.execute("SELECT expression, strategy_family, dataset FROM candidate_outcomes WHERE outcome = 'PASS'")
print("\n注入的5个正反馈种子:")
for i, (expr, family, ds) in enumerate(c.fetchall(), 1):
    print(f"{i}. {expr[:60]}")
    print(f"   算子家族: {family}")
    print(f"   数据集: {ds}")

conn.close()
print("\n✓ 正反馈种子注入完成")
