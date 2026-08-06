import pandas as pd
from datetime import datetime

# 读取待提交CSV
df = pd.read_csv('待提交Alpha列表.csv')

print("=== 当前CSV状态分析 ===")
print(f"总行数: {len(df)}")

# 统计各状态
print("\n队列状态分布:")
print(df['queue_status'].value_counts())

print("\n质量状态分布:")
print(df['quality_status'].value_counts())

# 检查旧合同问题
legacy_issue = df[df['last_error_category'] == 'LEGACY_CONTRACT_MISSING_EVIDENCE']
print(f"\n旧合同问题候选: {len(legacy_issue)} 行")
if len(legacy_issue) > 0:
    print("前3行示例:")
    print(legacy_issue[['candidate_id', 'queue_status', 'last_error_category']].head(3))

# 检查PENDING_SIMULATION
pending_sim = df[df['queue_status'] == 'PENDING_SIMULATION']
print(f"\nPENDING_SIMULATION候选: {len(pending_sim)} 行")
if len(pending_sim) > 0:
    print("详情:")
    print(pending_sim[['candidate_id', 'expression', 'local_quality_score']].to_string(index=False))

# 保存分析结果
analysis = {
    'timestamp': datetime.now().isoformat(),
    'total_rows': len(df),
    'legacy_issue_count': len(legacy_issue),
    'pending_simulation_count': len(pending_sim),
    'status_distribution': df['queue_status'].value_counts().to_dict()
}

import json
with open('csv_status_before_cleanup.json', 'w') as f:
    json.dump(analysis, f, indent=2)

print("\n分析结果已保存到 csv_status_before_cleanup.json")
