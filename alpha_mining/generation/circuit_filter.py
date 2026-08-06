"""
熔断预测器集成到生成流水线

在种子选择阶段应用熔断预测，过滤高风险种子
"""
from dataclasses import dataclass
from pathlib import Path
import sys

from alpha_mining.generation.circuit_breaker_predictor import RuleBasedCircuitPredictor


@dataclass(frozen=True)
class SeedWithRisk:
    """带熔断风险评分的种子"""
    seed: any
    circuit_risk_score: float
    circuit_risk_reason: str
    should_block: bool


def filter_seeds_by_circuit_risk(
    raw_seeds: list,
    snapshots: any,
    *,
    risk_threshold: float = 0.5,
    max_high_risk_seeds: int = 2,
) -> tuple[list, dict[str, int]]:
    """
    使用熔断预测器过滤种子

    Args:
        raw_seeds: 原始种子列表
        snapshots: LocalSnapshots对象（包含catalog和feedback）
        risk_threshold: 风险阈值，超过此值视为高风险
        max_high_risk_seeds: 允许通过的高风险种子数量上限

    Returns:
        (过滤后的种子列表, 拒绝统计字典)
    """
    predictor = RuleBasedCircuitPredictor()
    rejections = {}

    # 评估每个种子的熔断风险
    evaluated_seeds = []
    for seed in raw_seeds:
        # 提取种子的关键属性
        strategy_family = str(getattr(seed, "parent_template", "unknown"))
        dataset = str(getattr(seed, "dataset", ""))
        expression = str(getattr(seed, "expression", ""))

        # 如果没有明确dataset，从fields推断
        if not dataset:
            from alpha_mining.domain.expression_normalization import extract_fields
            fields = extract_fields(expression)
            if fields and hasattr(snapshots, "catalog"):
                field_datasets = {
                    snapshots.catalog.fields[f].dataset_id
                    for f in fields
                    if f in snapshots.catalog.fields
                }
                if len(field_datasets) == 1:
                    dataset = next(iter(field_datasets))

        # 预测熔断风险
        prediction = predictor.predict(
            strategy_family=strategy_family,
            dataset=dataset or "unknown",
            operator_topology=expression,
        )

        evaluated_seeds.append(SeedWithRisk(
            seed=seed,
            circuit_risk_score=prediction.risk_score,
            circuit_risk_reason=prediction.reason,
            should_block=prediction.should_block,
        ))

    # 排序：低风险优先
    evaluated_seeds.sort(key=lambda x: x.circuit_risk_score)

    # 应用过滤规则
    safe_seeds = []
    high_risk_count = 0

    for evaluated in evaluated_seeds:
        if evaluated.should_block:
            # 高风险种子：限制数量
            if high_risk_count < max_high_risk_seeds:
                safe_seeds.append(evaluated.seed)
                high_risk_count += 1
                print(
                    f"[CIRCUIT_RISK_ALLOWED] 允许高风险种子 "
                    f"(risk={evaluated.circuit_risk_score:.2f}, "
                    f"quota={high_risk_count}/{max_high_risk_seeds}): "
                    f"{evaluated.circuit_risk_reason}",
                    file=sys.stderr,
                )
            else:
                rejections["CIRCUIT_BREAKER_RISK_HIGH"] = (
                    rejections.get("CIRCUIT_BREAKER_RISK_HIGH", 0) + 1
                )
                print(
                    f"[CIRCUIT_RISK_BLOCKED] 熔断风险过高 "
                    f"(risk={evaluated.circuit_risk_score:.2f}): "
                    f"{evaluated.circuit_risk_reason}",
                    file=sys.stderr,
                )
        else:
            # 低风险种子：直接通过
            safe_seeds.append(evaluated.seed)

    if rejections:
        print(
            f"[CIRCUIT_FILTER_SUMMARY] 熔断过滤: "
            f"{len(raw_seeds)} seeds → {len(safe_seeds)} safe "
            f"({len(raw_seeds) - len(safe_seeds)} blocked)",
            file=sys.stderr,
        )

    return safe_seeds, rejections


# 使用示例（在HighQualityGenerator.generate中）：
"""
def generate(self, snapshots: LocalSnapshots, *, cycle_id: str, candidates_per_cycle: int) -> HighQualityResult:
    raw_seeds = list(self.kernel.generate(snapshots))

    # 【新增】熔断风险过滤
    from alpha_mining.generation.circuit_filter import filter_seeds_by_circuit_risk
    circuit_safe_seeds, circuit_rejections = filter_seeds_by_circuit_risk(
        raw_seeds,
        snapshots,
        risk_threshold=0.5,
        max_high_risk_seeds=2,  # 允许最多2个高风险种子通过
    )

    # 在原有_select_seeds基础上叠加熔断过滤结果
    seeds, seed_rejections = self._select_seeds(circuit_safe_seeds, snapshots)

    # 合并拒绝原因
    for reason, count in circuit_rejections.items():
        seed_rejections[reason] = seed_rejections.get(reason, 0) + count

    if not seeds:
        return HighQualityResult((), _empty_context(), (), seed_rejections, 0)

    # ... 后续LLM调用逻辑不变 ...
"""
