#!/usr/bin/env python3
"""
基于规则的熔断预测器
根据历史熔断模式，预测请求是否会触发熔断

核心发现：
1. analyst10/analyst11 数据集的熔断率极高（92%）
2. TOP3000 数据集安全（0%熔断）
3. 包含ts_delta/ts_rank/ts_zscore的复杂topology容易熔断
4. 简单策略（momentum, mean_reversion等）成功率高
"""
from dataclasses import dataclass
from typing import Literal


@dataclass
class CircuitBreakerPrediction:
    """熔断预测结果"""
    should_block: bool
    confidence: float  # 0.0 - 1.0
    reason: str
    risk_score: float  # 0.0 - 1.0，越高越危险


class RuleBasedCircuitPredictor:
    """基于规则的熔断预测器"""

    # 基于历史数据的风险权重
    HIGH_RISK_DATASETS = {"analyst10", "analyst11", "pv1"}  # 熔断率 92%+
    SAFE_DATASETS = {"TOP3000"}  # 熔断率 0%

    HIGH_RISK_OPERATORS = {"ts_delta", "ts_rank", "ts_zscore"}
    SAFE_FAMILIES = {
        "momentum", "mean_reversion", "correlation", "volume"
    }

    def predict(
        self,
        *,
        strategy_family: str,
        dataset: str,
        operator_topology: str = "",
        mechanism: str = "",
    ) -> CircuitBreakerPrediction:
        """
        预测请求是否会触发熔断

        Args:
            strategy_family: 策略家族名称
            dataset: 数据集名称
            operator_topology: 算子拓扑（可选）
            mechanism: 经济机制描述（可选）

        Returns:
            CircuitBreakerPrediction: 预测结果
        """
        risk_score = 0.0
        reasons = []

        # 规则1：数据集风险（权重降低，允许更多候选通过）
        if dataset in self.HIGH_RISK_DATASETS:
            risk_score += 0.3  # 从0.5降到0.3
            reasons.append(f"高风险数据集 {dataset}（历史熔断率>90%）")
        elif dataset in self.SAFE_DATASETS:
            risk_score -= 0.3
            reasons.append(f"安全数据集 {dataset}（历史熔断率0%）")
        else:
            risk_score += 0.1
            reasons.append(f"未知数据集 {dataset}")

        # 规则2：策略家族风险
        if strategy_family in self.SAFE_FAMILIES:
            risk_score -= 0.2
            reasons.append(f"安全策略家族 {strategy_family}")
        elif "#" in strategy_family:  # 包含占位符的模板
            risk_score += 0.2
            reasons.append(f"模板策略 {strategy_family}")

        # 规则3：算子复杂度风险（降低惩罚）
        if operator_topology:
            topology_depth = operator_topology.count("(")
            if topology_depth >= 2:
                risk_score += 0.15  # 从0.3降到0.15
                reasons.append(f"高复杂度算子（嵌套深度={topology_depth}）")

            # 检查高风险算子
            risky_ops = [
                op for op in self.HIGH_RISK_OPERATORS
                if op in operator_topology.lower()
            ]
            if risky_ops:
                risk_score += 0.1 * len(risky_ops)  # 从0.15降到0.1
                reasons.append(f"包含高风险算子: {', '.join(risky_ops)}")

        # 规则4：占位符数量（field#field# 说明未具体化）
        if "#" in (operator_topology or ""):
            placeholder_count = operator_topology.count("#")
            if placeholder_count >= 4:
                risk_score += 0.2
                reasons.append(f"过多占位符（{placeholder_count}个）")

        # 标准化风险分数到 [0, 1]
        risk_score = max(0.0, min(1.0, risk_score))

        # 决策阈值（提高到0.7，更宽松）
        BLOCK_THRESHOLD = 0.7  # 从0.5提高到0.7
        should_block = risk_score >= BLOCK_THRESHOLD

        # 计算置信度
        confidence = abs(risk_score - BLOCK_THRESHOLD) / BLOCK_THRESHOLD
        confidence = max(0.5, min(1.0, confidence))

        reason = " | ".join(reasons) if reasons else "无明显风险信号"

        return CircuitBreakerPrediction(
            should_block=should_block,
            confidence=confidence,
            reason=reason,
            risk_score=risk_score,
        )

    def explain_decision(self, prediction: CircuitBreakerPrediction) -> str:
        """生成人类可读的决策解释"""
        decision = "🔴 建议阻断" if prediction.should_block else "🟢 允许通过"
        return (
            f"{decision}\n"
            f"  风险分数: {prediction.risk_score:.2f} / 1.00\n"
            f"  置信度: {prediction.confidence:.0%}\n"
            f"  原因: {prediction.reason}"
        )


# 示例用法
if __name__ == "__main__":
    predictor = RuleBasedCircuitPredictor()

    print("=" * 70)
    print("熔断预测器测试")
    print("=" * 70)

    # 测试用例1：高风险组合（实际熔断的案例）
    test_cases = [
        {
            "name": "高风险：analyst10 + ts_delta",
            "strategy_family": "ts_delta(field#field#,#)",
            "dataset": "analyst10",
            "operator_topology": "ts_delta(field#field#,#)",
        },
        {
            "name": "安全：TOP3000 + momentum",
            "strategy_family": "momentum",
            "dataset": "TOP3000",
            "operator_topology": "",
        },
        {
            "name": "中等风险：analyst10 + rank",
            "strategy_family": "ts_rank(field#field#,#)",
            "dataset": "analyst10",
            "operator_topology": "ts_rank(field#field#,#)",
        },
        {
            "name": "高复杂度：嵌套算子",
            "strategy_family": "ts_zscore(ts_delta(field,#),#)",
            "dataset": "analyst11",
            "operator_topology": "ts_zscore(ts_delta(field,#),#)",
        },
        {
            "name": "安全：TOP3000 + correlation",
            "strategy_family": "correlation",
            "dataset": "TOP3000",
            "operator_topology": "",
        },
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"\n[测试 {i}] {case['name']}")
        print(f"  strategy_family: {case['strategy_family']}")
        print(f"  dataset: {case['dataset']}")
        print(f"  operator_topology: {case.get('operator_topology', 'N/A')}")

        prediction = predictor.predict(
            strategy_family=case["strategy_family"],
            dataset=case["dataset"],
            operator_topology=case.get("operator_topology", ""),
        )

        print(f"\n{predictor.explain_decision(prediction)}")
        print()

    print("=" * 70)
    print("测试完成")
    print("=" * 70)
