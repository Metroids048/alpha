#!/usr/bin/env python3
"""
熔断预测模型训练 - 处理不平衡数据和稀疏特征

当前数据状态：
- 26条总记录：4条PASS，1条NEAR_PASS，21条FAILED
- PASS记录缺少operator_topology等关键特征
- FAILED记录中大量是熔断错误
"""
import sqlite3
import json
from pathlib import Path
from collections import Counter
from typing import Any

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")

def extract_features(row: tuple[Any, ...]) -> dict[str, Any]:
    """从candidate_outcomes记录中提取特征"""
    (
        outcome, strategy_family, mechanism, dataset,
        operator_topology, field_skeleton, parameter_skeleton,
        error_category, error_message, sharpe, fitness, turnover
    ) = row

    features = {
        "outcome": outcome,
        "strategy_family": strategy_family or "unknown",
        "dataset": dataset or "unknown",
        "has_mechanism": bool(mechanism and mechanism.strip()),
        "has_operator_topology": bool(operator_topology and operator_topology.strip()),
        "has_field_skeleton": bool(field_skeleton and field_skeleton.strip()),
        "has_parameter_skeleton": bool(parameter_skeleton and parameter_skeleton.strip()),
        "error_category": error_category or "",
        "is_circuit_error": "CircuitOpen" in (error_message or ""),
    }

    # 提取operator_topology的复杂度特征
    if operator_topology:
        features["topology_depth"] = operator_topology.count("(")
        features["topology_has_delta"] = "delta" in operator_topology.lower()
        features["topology_has_rank"] = "rank" in operator_topology.lower()
        features["topology_has_zscore"] = "zscore" in operator_topology.lower()
    else:
        features["topology_depth"] = 0
        features["topology_has_delta"] = False
        features["topology_has_rank"] = False
        features["topology_has_zscore"] = False

    # 性能指标
    if sharpe is not None:
        features["sharpe"] = float(sharpe)
    if fitness is not None:
        features["fitness"] = float(fitness)
    if turnover is not None:
        features["turnover"] = float(turnover)

    return features


def analyze_circuit_breaker_patterns():
    """分析导致熔断的模式"""
    with sqlite3.connect(database) as con:
        print("=" * 60)
        print("熔断预测模型 - 数据分析")
        print("=" * 60)

        # 1. 加载所有反馈
        rows = con.execute("""
            SELECT
                outcome,
                strategy_family,
                mechanism,
                dataset,
                operator_topology,
                field_skeleton,
                parameter_skeleton,
                error_category,
                error_message,
                sharpe,
                fitness,
                turnover
            FROM candidate_outcomes
            ORDER BY observed_at DESC
        """).fetchall()

        print(f"\n总记录数: {len(rows)}")

        # 2. 提取特征
        samples = [extract_features(row) for row in rows]

        # 3. 统计熔断模式
        circuit_errors = [s for s in samples if s["is_circuit_error"]]
        print(f"熔断错误数: {len(circuit_errors)}")

        if circuit_errors:
            print("\n熔断错误的特征分布：")
            print(f"  strategy_family: {Counter(s['strategy_family'] for s in circuit_errors).most_common(5)}")
            print(f"  dataset: {Counter(s['dataset'] for s in circuit_errors).most_common(5)}")
            print(f"  topology_depth: {Counter(s['topology_depth'] for s in circuit_errors).most_common(5)}")
            print(f"  有delta算子: {sum(1 for s in circuit_errors if s['topology_has_delta'])} / {len(circuit_errors)}")
            print(f"  有rank算子: {sum(1 for s in circuit_errors if s['topology_has_rank'])} / {len(circuit_errors)}")
            print(f"  有zscore算子: {sum(1 for s in circuit_errors if s['topology_has_zscore'])} / {len(circuit_errors)}")

        # 4. 统计成功模式
        successful = [s for s in samples if s["outcome"] == "PASS"]
        print(f"\n成功案例数: {len(successful)}")

        if successful:
            print("\n成功案例的特征分布：")
            print(f"  strategy_family: {Counter(s['strategy_family'] for s in successful).most_common(5)}")
            print(f"  dataset: {Counter(s['dataset'] for s in successful).most_common(5)}")
            print(f"  平均sharpe: {sum(s.get('sharpe', 0) for s in successful) / len(successful):.3f}")
            print(f"  平均fitness: {sum(s.get('fitness', 0) for s in successful) / len(successful):.3f}")

        # 5. 识别高风险组合
        print("\n=" * 60)
        print("高风险组合识别")
        print("=" * 60)

        # 统计每个dataset+family组合的失败率
        combinations = {}
        for s in samples:
            key = (s["dataset"], s["strategy_family"])
            if key not in combinations:
                combinations[key] = {"total": 0, "failed": 0, "circuit": 0}
            combinations[key]["total"] += 1
            if s["outcome"] == "FAILED":
                combinations[key]["failed"] += 1
            if s["is_circuit_error"]:
                combinations[key]["circuit"] += 1

        print("\n高失败率组合（按熔断错误排序）：")
        sorted_combos = sorted(
            combinations.items(),
            key=lambda x: (x[1]["circuit"], x[1]["failed"]),
            reverse=True
        )

        for (dataset, family), stats in sorted_combos[:10]:
            fail_rate = stats["failed"] / stats["total"] * 100
            circuit_rate = stats["circuit"] / stats["total"] * 100
            print(f"  {dataset:12} + {family:30} | "
                  f"总数={stats['total']:2} | "
                  f"失败率={fail_rate:5.1f}% | "
                  f"熔断率={circuit_rate:5.1f}%")

        # 6. 推荐策略
        print("\n=" * 60)
        print("熔断策略推荐")
        print("=" * 60)

        total_circuit = sum(1 for s in samples if s["is_circuit_error"])
        if total_circuit > len(samples) * 0.5:
            print("\n⚠️  当前熔断率过高 (>50%)")
            print("   建议：")
            print("   1. 降低熔断敏感度")
            print("   2. 增加恢复探测的间隔时间")
            print("   3. 检查是否有资源瓶颈")

        # 识别安全的family
        safe_families = set()
        for family in set(s["strategy_family"] for s in samples):
            family_samples = [s for s in samples if s["strategy_family"] == family]
            circuit_rate = sum(1 for s in family_samples if s["is_circuit_error"]) / len(family_samples)
            if circuit_rate < 0.3:  # 熔断率<30%
                safe_families.add(family)

        if safe_families:
            print(f"\n✓ 低风险策略家族（熔断率<30%）：")
            for family in sorted(safe_families):
                print(f"    - {family}")

        # 7. 数据质量评估
        print("\n=" * 60)
        print("训练数据质量评估")
        print("=" * 60)

        complete_features = sum(1 for s in samples if all([
            s["has_operator_topology"],
            s["has_field_skeleton"],
            s["has_parameter_skeleton"]
        ]))

        print(f"\n特征完整性: {complete_features}/{len(samples)} ({complete_features/len(samples)*100:.1f}%)")

        if len(successful) < 10:
            print(f"\n⚠️  正样本不足 (仅{len(successful)}条)")
            print("   当前数据不足以训练可靠的预测模型")
            print("   建议：")
            print("   1. 收集更多成功案例（目标>=20条）")
            print("   2. 使用规则引擎而非机器学习模型")
            print("   3. 基于当前熔断模式设置简单的阈值规则")

        return samples


if __name__ == "__main__":
    samples = analyze_circuit_breaker_patterns()

    print("\n" + "=" * 60)
    print("数据已加载，可用于后续建模")
    print("=" * 60)
    print(f"总样本数: {len(samples)}")
    print(f"特征维度: {len(samples[0])} 个特征")
