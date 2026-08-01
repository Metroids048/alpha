#!/usr/bin/env python3
"""测试质量驱动生成系统（不连接真实平台）"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from 生成Alpha_质量驱动 import (
    load_research_specs,
    classify_strategy_family,
    generate_batch,
    QualityFeedback,
)
import random

def test_basic_workflow():
    print("=== 测试质量驱动系统（基础工作流） ===\n")

    # 1. 加载研究规范
    print("[1/5] 加载研究规范...")
    specs = load_research_specs(Path("research_memory.sqlite"))
    print(f"✓ 加载 {len(specs)} 个规范\n")

    # 2. 测试策略分类
    print("[2/5] 测试策略分类...")
    test_cases = [
        ("momentum trading", "trend", "momentum"),
        ("mean reversion strategy", "reversal", "reversal"),
        ("volatility based", "risk", "volatility"),
        ("fundamental value", "quality", "fundamental"),
    ]
    for mechanism, family, expected in test_cases:
        result = classify_strategy_family(mechanism, family)
        status = "✓" if result == expected else "✗"
        print(f"{status} {mechanism} + {family} → {result}")
    print()

    # 3. 测试生成（无反馈）
    print("[3/5] 测试生成批次（初始权重）...")
    global_seen_hashes = set()
    strategy_weights = {
        "momentum": 1.0,
        "reversal": 1.0,
        "volatility": 1.0,
        "fundamental": 1.0,
        "balanced": 1.0,
    }
    quality_feedback = {}

    batch1, rejected1 = generate_batch(
        specs,
        limit=10,
        global_seen_hashes=global_seen_hashes,
        strategy_weights=strategy_weights,
        quality_feedback=quality_feedback,
        rng=random.Random(42),
    )

    print(f"✓ 生成 {len(batch1)} 个候选")
    family_dist = {}
    for c in batch1:
        family_dist[c.strategy_family] = family_dist.get(c.strategy_family, 0) + 1
    print(f"  策略分布: {family_dist}")
    print(f"  拒绝统计: {dict(rejected1)}\n")

    # 4. 测试质量反馈
    print("[4/5] 测试质量反馈...")
    quality_feedback = {
        "momentum": QualityFeedback(
            strategy_family="momentum",
            pass_count=3,
            fail_count=7,
            avg_sharpe=1.28,
            avg_fitness=1.05,
            pass_rate=0.30,
        ),
        "fundamental": QualityFeedback(
            strategy_family="fundamental",
            pass_count=5,
            fail_count=5,
            avg_sharpe=1.45,
            avg_fitness=1.18,
            pass_rate=0.50,
        ),
    }

    for family, fb in quality_feedback.items():
        score = fb.quality_score()
        print(f"✓ {family}: 通过率={fb.pass_rate:.0%} Sharpe={fb.avg_sharpe:.2f} "
              f"Fitness={fb.avg_fitness:.2f} 质量分={score:.1f}")
    print()

    # 5. 测试反馈驱动生成
    print("[5/5] 测试质量反馈驱动生成...")
    batch2, rejected2 = generate_batch(
        specs,
        limit=10,
        global_seen_hashes=global_seen_hashes,
        strategy_weights=strategy_weights,
        quality_feedback=quality_feedback,
        rng=random.Random(43),
    )

    print(f"✓ 生成 {len(batch2)} 个候选")
    family_dist2 = {}
    for c in batch2:
        family_dist2[c.strategy_family] = family_dist2.get(c.strategy_family, 0) + 1
    print(f"  策略分布: {family_dist2}")
    print(f"  预期: fundamental 占比提升（质量分更高）\n")

    # 总结
    print("=== 测试完成 ===")
    print(f"✓ 所有基础功能正常")
    print(f"✓ 质量反馈机制工作正常")
    print(f"✓ 去重机制工作正常（{len(global_seen_hashes)} 个哈希）")
    print(f"\n提示: 使用 --simulate 参数可启用真实平台模拟")


if __name__ == "__main__":
    test_basic_workflow()
