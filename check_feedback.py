#!/usr/bin/env python3
"""检查feedback表和生成链路"""
import sqlite3
from pathlib import Path
import json

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

print("=== feedback表状态 ===")
c.execute("SELECT COUNT(*) FROM feedback")
total = c.fetchone()[0]
print(f"总反馈数: {total}")

c.execute("SELECT sentiment, COUNT(*) FROM feedback GROUP BY sentiment")
for sentiment, cnt in c.fetchall():
    print(f"  {sentiment}: {cnt}")

print("\n=== 最近3条feedback ===")
c.execute("""
    SELECT candidate_id, alpha_id, sentiment, quality_score, payload_json, created_at
    FROM feedback
    ORDER BY created_at DESC
    LIMIT 3
""")
for cid, aid, sentiment, quality, payload, created in c.fetchall():
    try:
        p = json.loads(payload) if payload else {}
        expr = p.get('expression', '(无)')[:50]
    except:
        expr = '(解析失败)'
    print(f"\n{created}")
    print(f"  candidate: {cid[:20] if cid else '(无)'}...")
    print(f"  alpha: {aid[:20] if aid else '(无)'}...")
    print(f"  sentiment: {sentiment}, quality: {quality}")
    print(f"  expression: {expr}")

print("\n=== 检查LLM生成prompt使用的feedback ===")
# 看看生成代码如何读取feedback
import sys
sys.path.insert(0, 'C:/Users/Windows11/Desktop/alpha')
try:
    from alpha_mining.generator.alpha_generator_deepseek import LLMAlphaGenerator
    print("LLMAlphaGenerator imported")
    # 无法直接查看内部逻辑，需要看代码
except Exception as e:
    print(f"导入失败: {e}")

conn.close()
