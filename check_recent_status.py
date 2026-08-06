import sqlite3
con = sqlite3.connect('research_memory.sqlite')

# 检查candidate_work_items中的所有状态
print("candidate_work_items 状态统计:")
query = """
SELECT state, COUNT(*) as cnt
FROM candidate_work_items
GROUP BY state
ORDER BY cnt DESC
"""
for row in con.execute(query):
    print(f"  {row[0]}: {row[1]}")

print("\ncandidate_outcomes 结果统计:")
query2 = """
SELECT outcome, COUNT(*) as cnt
FROM candidate_outcomes
GROUP BY outcome
ORDER BY cnt DESC
"""
for row in con.execute(query2):
    print(f"  {row[0]}: {row[1]}")

# 查看最近的work_items
print("\n最近10个candidate_work_items:")
query3 = """
SELECT candidate_id, state, last_error_category, substr(last_error, 1, 80) as error_preview, updated_at
FROM candidate_work_items
ORDER BY updated_at DESC
LIMIT 10
"""
for row in con.execute(query3):
    print(f"  {row[0][:12]}.. | {row[1]:20s} | {row[2] or 'NULL':30s} | {row[3] or ''}")

# 查看最近的outcomes
print("\n最近10个candidate_outcomes:")
query4 = """
SELECT candidate_id, outcome, error_category, substr(error_message, 1, 60) as msg, observed_at
FROM candidate_outcomes
ORDER BY observed_at DESC
LIMIT 10
"""
for row in con.execute(query4):
    print(f"  {row[0][:12]}.. | {row[1]:15s} | {row[2] or 'NULL':30s} | {row[3] or ''}")

con.close()
