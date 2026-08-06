#!/usr/bin/env python3
"""测试load_feedback_summary函数"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from alpha_mining.generation.snapshots import load_feedback_summary

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
queue_path = Path("数据/本地运行产物/队列/candidate_work_items.csv")

print("=== 测试load_feedback_summary ===")
feedback = load_feedback_summary(database, queue_path=queue_path)

print(f"\n总记录数: {len(feedback.records)}")
print(f"positive (PASS): {len(feedback.positive)}")
print(f"near_pass: {len(feedback.near_pass)}")
print(f"failures: {len(feedback.failures)}")

print("\n=== positive记录详情 ===")
for i, rec in enumerate(feedback.positive, 1):
    print(f"\n{i}. {rec.ref_id}")
    print(f"   expression: {rec.expression[:80]}...")
    print(f"   outcome: {rec.outcome}")
    print(f"   family: {rec.family}")
    print(f"   grounded: {rec.grounded}")
    print(f"   failure_types: {rec.failure_types}")

print("\n=== failures记录详情（前5条）===")
for i, rec in enumerate(feedback.failures[:5], 1):
    print(f"\n{i}. {rec.ref_id}")
    print(f"   expression: {rec.expression[:60] if rec.expression else '(无)'}...")
    print(f"   outcome: {rec.outcome}")
    print(f"   family: {rec.family}")
    print(f"   failure_types: {rec.failure_types}")
