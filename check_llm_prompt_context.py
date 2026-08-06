#!/usr/bin/env python3
"""检查LLM生成prompt中是否包含positive feedback"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from alpha_mining.generation.snapshots import load_feedback_summary, load_local_snapshots
from alpha_mining.generation.portfolio import build_portfolio
from alpha_mining.generation.llm_generation import build_prompt

# 加载snapshots
root = Path(".")
snapshots = load_local_snapshots(root)

print("=== Snapshots加载状态 ===")
print(f"feedback.positive: {len(snapshots.feedback.positive)}")
print(f"feedback.failures: {len(snapshots.feedback.failures)}")

# 构建portfolio
portfolio = build_portfolio(snapshots)
print(f"\n=== Portfolio状态 ===")
print(f"accepted: {len(portfolio.accepted)}")

# 构建prompt
from alpha_mining.generation.llm_generation import _build_base_context

context = _build_base_context(snapshots, portfolio)

print(f"\n=== Context内容检查 ===")
print(f"context keys: {list(context.keys())}")

if "positive_examples" in context:
    print(f"\npositive_examples数量: {len(context['positive_examples'])}")
    print("\npositive_examples内容:")
    for i, ex in enumerate(context["positive_examples"], 1):
        print(f"  {i}. {ex.get('expression', '(无)')[:60]}...")
        print(f"     family: {ex.get('family', '(无)')}")
else:
    print("\n⚠️ context中没有positive_examples字段！")

# 检查prompt文本
try:
    prompt_parts = []
    if "positive_examples" in context and context["positive_examples"]:
        prompt_parts.append("=== 成功案例示例 ===")
        for ex in context["positive_examples"][:3]:
            prompt_parts.append(f"表达式: {ex.get('expression', '(无)')}")
            prompt_parts.append(f"家族: {ex.get('family', '(无)')}")

    print("\n=== Prompt片段 ===")
    print("\n".join(prompt_parts) if prompt_parts else "⚠️ prompt中没有成功案例")
except Exception as e:
    print(f"\n构建prompt失败: {e}")
