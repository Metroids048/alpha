import pandas as pd
import json
from datetime import datetime

# 备份原始CSV
df_original = pd.read_csv('待提交Alpha列表.csv')
df_original.to_csv('待提交Alpha列表_backup_20260806.csv', index=False)
print(f"✅ 已备份原始CSV到: 待提交Alpha列表_backup_20260806.csv")

# 读取CSV
df = pd.read_csv('待提交Alpha列表.csv')
print(f"\n清理前: {len(df)} 行")

# 统计清理前状态
before_stats = {
    'total': len(df),
    'legacy_issue': len(df[df['last_error_category'] == 'LEGACY_CONTRACT_MISSING_EVIDENCE']),
    'pending_simulation': len(df[df['queue_status'] == 'PENDING_SIMULATION']),
    'rejected': len(df[df['queue_status'].str.contains('REJECTED', na=False)])
}

# 清理旧合同候选（标记为REJECTED_OBSOLETE）
legacy_mask = df['last_error_category'] == 'LEGACY_CONTRACT_MISSING_EVIDENCE'
df.loc[legacy_mask, 'queue_status'] = 'REJECTED_OBSOLETE'
df.loc[legacy_mask, 'quality_status'] = 'REJECTED_OBSOLETE'
df.loc[legacy_mask, 'updated_at'] = datetime.now().isoformat() + 'Z'

print(f"✅ 已标记 {before_stats['legacy_issue']} 行旧合同候选为 REJECTED_OBSOLETE")

# 方案：将PENDING_SIMULATION候选移动到单独文件，保留主CSV干净
pending_mask = df['queue_status'] == 'PENDING_SIMULATION'
df_pending = df[pending_mask].copy()
df_pending.to_csv('待simulate候选_临时队列.csv', index=False)
print(f"✅ 已将 {len(df_pending)} 行PENDING_SIMULATION候选移动到独立文件")

# 从主CSV移除PENDING（它们需要simulate后才能重新入队）
df_clean = df[~pending_mask].copy()

# 保存清理后的CSV
df_clean.to_csv('待提交Alpha列表.csv', index=False)

after_stats = {
    'total': len(df_clean),
    'rejected_obsolete': len(df_clean[df_clean['queue_status'] == 'REJECTED_OBSOLETE']),
    'pending_simulation_moved': len(df_pending)
}

print(f"\n清理后: {len(df_clean)} 行")
print(f"  - REJECTED_OBSOLETE: {after_stats['rejected_obsolete']}")
print(f"  - PENDING_SIMULATION已移至独立文件: {after_stats['pending_simulation_moved']}")

# 保存清理报告
cleanup_report = {
    'timestamp': datetime.now().isoformat(),
    'action': 'stage1_cleanup',
    'before': before_stats,
    'after': after_stats,
    'changes': {
        'legacy_marked_obsolete': before_stats['legacy_issue'],
        'pending_moved_to_separate_file': before_stats['pending_simulation']
    }
}

with open('cleanup_report_stage1.json', 'w') as f:
    json.dump(cleanup_report, f, indent=2)

print("\n📊 清理报告已保存到: cleanup_report_stage1.json")
print("\n下一步: 注入反馈种子到数据库/CSV")
