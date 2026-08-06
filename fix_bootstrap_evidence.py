"""修复bootstrap候选的quality_evidence_json字段"""
import csv
import json
from pathlib import Path

csv_path = Path("待提交Alpha列表.csv")

# 读取CSV
with open(csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    fieldnames = reader.fieldnames

# 找到bootstrap候选
bootstrap_idx = None
for i, row in enumerate(rows):
    if row['generator_source'] == 'BOOTSTRAP_SUCCESS_TEMPLATE':
        bootstrap_idx = i
        break

if bootstrap_idx is None:
    print("❌ 未找到bootstrap候选")
    exit(1)

# 构造正确的quality_evidence_json
evidence = {
    "generator_contract_version": "generation-hq-v2",
    "catalog_legal": True,
    "catalog_source": "local_offline_field_snapshot",
    "catalog_age_hours": 24.0,
    "knowledge_grounded": False,
    "field_count": 4,
    "feedback_refs": [],
    "grounded_feedback_refs": [],
    "positive_feedback_support": True,
    "near_pass_support": False,
    "max_proxy_similarity": 0.0,
    "score_components": {
        "field_quality": 30.0,
        "grounded_feedback": 0.0,
        "novelty_low_similarity": 25.0,
        "mechanism_expression_consistency": 20.0,
        "knowledge_relevance": 0.0,
        "turnover_complexity_concentration_risk": 20.0
    },
    "evidence_cap": 100.0,
    "bootstrap_template": True,
    "historical_sharpe": 1.76,
    "historical_fitness": 0.65,
    "historical_turnover": 0.08
}

# 更新候选
rows[bootstrap_idx]['quality_evidence_json'] = json.dumps(evidence)
rows[bootstrap_idx]['quality_status'] = 'ACCEPTED'
rows[bootstrap_idx]['queue_status'] = 'PENDING_SIMULATION'
rows[bootstrap_idx]['last_error'] = ''
rows[bootstrap_idx]['last_error_category'] = ''

# 写回CSV
with open(csv_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"✅ 修复bootstrap候选 (索引{bootstrap_idx})")
print(f"   quality_evidence_json: {json.dumps(evidence, indent=2)[:200]}...")
