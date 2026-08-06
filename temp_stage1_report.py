import json
from datetime import datetime

# 阶段1验证报告
stage1_results = {
    'timestamp': datetime.now().isoformat(),
    'stage': 'stage1_verification',
    'before': {
        'feedback': 0,
        'positive': 0,
        'near_pass': 0,
        'enqueued': 0,
        'pending': 5,
        'rejected': 25,
        'top_rejection': 'SEED_TOPOLOGY_DUPLICATE:10'
    },
    'after': {
        'feedback': 1,
        'positive': 0,
        'near_pass': 1,
        'enqueued': 0,  # 仍未入队
        'pending': 5,
        'rejected': 24,
        'top_rejections': {
            'SEED_TOPOLOGY_DUPLICATE': 10,  # 未改善
            'LLM_CRITIQUE_REJECTED': 5,  # 新增问题
            'UNKNOWN_OPERATOR': 3,
            'PLAN_CROSS_DATASET': 2,
            'PLAN_UNKNOWN_FIELD': 2
        }
    },
    'improvements': [
        '✅ 反馈种子生效: feedback 0→1, near_pass 0→1',
        '✅ dual-ledger冲突已解决',
        '✅ 旧合同候选已清理'
    ],
    'remaining_issues': [
        '❌ 种子重复仍高: SEED_TOPOLOGY_DUPLICATE:10 (41%)',
        '❌ LLM批评拒绝: 5个候选因不符合计划被拒',
        '❌ 仍未入队: enqueued=0'
    ],
    'next_actions': [
        '阶段2: 种子去重强化（扩展种子池 + 拓扑去重）',
        '修复LLM_CRITIQUE问题（算子与计划不匹配）'
    ]
}

with open('stage1_verification_report.json', 'w') as f:
    json.dump(stage1_results, f, indent=2)

print("=== 阶段1验证报告 ===")
print("\n改善:")
for item in stage1_results['improvements']:
    print(f"  {item}")

print("\n剩余问题:")
for item in stage1_results['remaining_issues']:
    print(f"  {item}")

print("\n下一步:")
for item in stage1_results['next_actions']:
    print(f"  {item}")

print("\n分析:")
print("- 反馈种子已生效，LLM可以引用历史NEAR_PASS样本")
print("- 但种子重复问题仍严重（10/24=41%），阻止了入队")
print("- 新增LLM_CRITIQUE_REJECTED：候选不符合研究计划约束")

print("\n立即进入阶段2: 种子去重强化")
