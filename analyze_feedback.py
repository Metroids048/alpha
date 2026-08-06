import sqlite3
import json
import pandas as pd

# 连接数据库
conn = sqlite3.connect('数据/本地运行产物/数据库/research_memory.sqlite')

# 查询所有反馈
query = """
SELECT
    expression,
    outcome,
    sharpe,
    fitness,
    turnover,
    margin,
    drawdown,
    check_correlation,
    rejection_reason,
    platform_message,
    submission_timestamp
FROM submission_observations
ORDER BY submission_timestamp DESC
LIMIT 50
"""

df = pd.read_sql_query(query, conn)
conn.close()

print("=" * 80)
print("历史提交反馈分析")
print("=" * 80)

# 统计outcome分布
print("\n【Outcome分布】")
print(df['outcome'].value_counts())

# 分析FAR_FAIL的原因
far_fails = df[df['outcome'] == 'FAR_FAIL']
print(f"\n【FAR_FAIL详情】共{len(far_fails)}条")
print("\nSharpe分布:")
print(far_fails['sharpe'].describe())
print("\nFitness分布:")
print(far_fails['fitness'].describe())
print("\nTurnover分布:")
print(far_fails['turnover'].describe())

# 找出成功案例
success = df[df['outcome'].isin(['PASS', 'NEAR_PASS'])]
print(f"\n【成功案例】共{len(success)}条")
if len(success) > 0:
    print("\n成功案例表达式:")
    for idx, row in success.iterrows():
        print(f"  - {row['expression']}")
        print(f"    Sharpe={row['sharpe']:.2f}, Fitness={row['fitness']:.2f}, Turnover={row['turnover']:.2f}%")

# 分析拒绝原因
print("\n【拒绝原因统计】")
rejection_counts = far_fails['rejection_reason'].value_counts()
print(rejection_counts.head(10))

# 保存详细报告
report = {
    "total_submissions": len(df),
    "outcome_distribution": df['outcome'].value_counts().to_dict(),
    "far_fail_stats": {
        "count": len(far_fails),
        "sharpe_mean": float(far_fails['sharpe'].mean()) if len(far_fails) > 0 else None,
        "fitness_mean": float(far_fails['fitness'].mean()) if len(far_fails) > 0 else None,
        "turnover_mean": float(far_fails['turnover'].mean()) if len(far_fails) > 0 else None,
    },
    "success_expressions": success['expression'].tolist() if len(success) > 0 else [],
    "top_rejection_reasons": rejection_counts.head(5).to_dict() if len(far_fails) > 0 else {}
}

with open('feedback_analysis.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print("\n分析报告已保存到 feedback_analysis.json")
