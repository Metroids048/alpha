import sqlite3
import json
from datetime import datetime, timezone

conn = sqlite3.connect('research_memory.sqlite')
cursor = conn.cursor()

print("=== 检查candidate_outcomes表 ===")
cursor.execute("SELECT COUNT(*) FROM candidate_outcomes")
total = cursor.fetchone()[0]
print(f"总记录数: {total}")

# 检查正例（PASS或READY_TO_SUBMIT）
cursor.execute("""
    SELECT COUNT(*)
    FROM candidate_outcomes
    WHERE outcome IN ('PASS', 'READY_TO_SUBMIT', 'NEAR_PASS')
""")
positive_count = cursor.fetchone()[0]
print(f"正例数量: {positive_count}")

# 检查高质量候选（Sharpe>1.5或Fitness>0.6）
cursor.execute("""
    SELECT
        expression,
        outcome,
        sharpe,
        fitness,
        turnover,
        strategy_family,
        parent_template,
        observed_at
    FROM candidate_outcomes
    WHERE (sharpe > 1.5 OR fitness > 0.6)
       OR outcome IN ('PASS', 'READY_TO_SUBMIT')
    ORDER BY sharpe DESC, fitness DESC
    LIMIT 10
""")
high_quality = cursor.fetchall()

print(f"\n高质量历史候选: {len(high_quality)} 条")
if high_quality:
    print("\n前3条最佳:")
    for i, row in enumerate(high_quality[:3], 1):
        print(f"\n{i}. Expression: {row[0][:80]}...")
        print(f"   Outcome: {row[1]}, Sharpe: {row[2]}, Fitness: {row[3]}")
        print(f"   Strategy: {row[5][:60] if row[5] else 'N/A'}")
else:
    print("\n⚠️ 没有找到高质量历史候选")

# 检查NEAR_PASS（可能的种子来源）
cursor.execute("""
    SELECT
        expression,
        sharpe,
        fitness,
        strategy_family
    FROM candidate_outcomes
    WHERE outcome = 'NEAR_PASS'
    ORDER BY sharpe DESC
    LIMIT 5
""")
near_pass = cursor.fetchall()
print(f"\nNEAR_PASS候选: {len(near_pass)} 条")
if near_pass:
    for i, row in enumerate(near_pass[:3], 1):
        print(f"{i}. {row[0][:80]}... (Sharpe: {row[1]}, Fitness: {row[2]})")

# 保存分析结果
analysis = {
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'total_outcomes': total,
    'positive_count': positive_count,
    'high_quality_count': len(high_quality),
    'near_pass_count': len(near_pass),
    'has_seeds': len(high_quality) > 0 or len(near_pass) > 0
}

with open('feedback_seed_analysis.json', 'w') as f:
    json.dump(analysis, f, indent=2)

print(f"\n分析结果已保存到: feedback_seed_analysis.json")

conn.close()
