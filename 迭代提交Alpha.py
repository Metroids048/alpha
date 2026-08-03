#!/usr/bin/env python3
"""
迭代提交Alpha - 带反馈学习的完整流程

核心逻辑：
1. 提交候选到平台simulate
2. 收集失败原因（sharpe低、fitness低、turnover高等）
3. 将失败原因反馈给LLM，生成改进的alpha
4. 重复迭代直到达到质量标准

使用方法：
    python 迭代提交Alpha.py --max-iterations 5 --target-count 10

参数：
    --max-iterations N   最大迭代轮数（默认5）
    --target-count N     目标通过数量（默认10）
    --min-sharpe FLOAT   最低Sharpe要求（默认1.58）
    --min-fitness FLOAT  最低Fitness要求（默认1.0）
    --max-turnover FLOAT 最高Turnover要求（默认0.7）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.platform.gateway import PlatformGateway
from alpha_mining.generation.generator import AlphaGenerator
from alpha_mining.domain.validation import LocalExpressionValidator


class IterativeSubmitter:
    """带反馈学习的迭代提交器"""

    def __init__(
        self,
        *,
        min_sharpe: float = 1.58,
        min_fitness: float = 1.0,
        max_turnover: float = 0.7,
        max_iterations: int = 5,
        target_count: int = 10,
    ):
        self.min_sharpe = min_sharpe
        self.min_fitness = min_fitness
        self.max_turnover = max_turnover
        self.max_iterations = max_iterations
        self.target_count = target_count

        self.gateway = PlatformGateway()
        self.generator = AlphaGenerator()
        self.validator = LocalExpressionValidator()

        self.feedback_db: list[dict] = []  # 历史反馈数据库
        self.passed_alphas: list[dict] = []  # 通过的alpha
        self.iteration_stats: list[dict] = []  # 每轮统计

    def load_candidates(self, csv_path: Path) -> list[dict]:
        """加载候选表达式"""
        candidates = []
        with csv_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                candidates.append({
                    "id": row.get("候选ID") or row.get("id"),
                    "expression": row.get("表达式") or row.get("expression"),
                    "score": float(row.get("分数") or row.get("score") or 0),
                    "source": row.get("来源") or row.get("source") or "unknown",
                })
        return candidates

    def simulate_batch(self, candidates: list[dict]) -> list[dict]:
        """批量simulate并收集结果"""
        results = []
        for i, cand in enumerate(candidates, 1):
            print(f"  [{i}/{len(candidates)}] simulate: {cand['id'][:20]}...")

            try:
                resp = self.gateway.simulate(
                    expression=cand["expression"],
                    settings={
                        "region": "USA",
                        "universe": "TOP3000",
                        "delay": 1,
                        "decay": 0,
                        "neutralization": "SUBINDUSTRY",
                        "truncation": 0.08,
                        "pasteurization": "ON",
                        "unitHandling": "VERIFY",
                        "nanHandling": "OFF",
                        "language": "FASTEXPR",
                    }
                )

                alpha_id = resp.get("alpha")
                is_data = resp.get("is", {})

                # 提取指标
                sharpe = is_data.get("sharpe")
                fitness = is_data.get("fitness")
                turnover = is_data.get("turnover")
                returns = is_data.get("returns")

                # 提取失败原因
                checks = is_data.get("checks", [])
                failure_reasons = []
                for check in checks:
                    if check.get("result") == "FAIL":
                        name = check.get("name", "")
                        value = check.get("value")
                        limit = check.get("limit")
                        message = check.get("message", "")
                        failure_reasons.append(f"{name}: {value} (limit: {limit}) - {message}")

                # 判断是否通过
                passed = (
                    sharpe is not None and sharpe >= self.min_sharpe and
                    fitness is not None and fitness >= self.min_fitness and
                    turnover is not None and turnover <= self.max_turnover
                )

                result = {
                    "id": cand["id"],
                    "expression": cand["expression"],
                    "alpha_id": alpha_id,
                    "sharpe": sharpe,
                    "fitness": fitness,
                    "turnover": turnover,
                    "returns": returns,
                    "passed": passed,
                    "failure_reasons": failure_reasons,
                    "timestamp": datetime.now().isoformat(),
                }

                results.append(result)

                if passed:
                    print(f"    ✅ PASS: sharpe={sharpe:.2f}, fitness={fitness:.2f}, turnover={turnover:.2%}")
                else:
                    print(f"    ❌ FAIL: sharpe={sharpe:.2f}, fitness={fitness:.2f}, turnover={turnover:.2%}")
                    if failure_reasons:
                        print(f"    失败原因: {'; '.join(failure_reasons[:2])}")

            except Exception as e:
                print(f"    ❌ ERROR: {e}")
                results.append({
                    "id": cand["id"],
                    "expression": cand["expression"],
                    "passed": False,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })

            time.sleep(2.0)  # 速率限制

        return results

    def analyze_failures(self, results: list[dict]) -> dict:
        """分析失败原因，生成改进建议"""
        failures = [r for r in results if not r.get("passed")]

        if not failures:
            return {"has_failures": False}

        # 统计失败原因分布
        reason_counts = defaultdict(int)
        sharpe_values = []
        fitness_values = []
        turnover_values = []

        for f in failures:
            if f.get("sharpe") is not None:
                sharpe_values.append(f["sharpe"])
            if f.get("fitness") is not None:
                fitness_values.append(f["fitness"])
            if f.get("turnover") is not None:
                turnover_values.append(f["turnover"])

            for reason in f.get("failure_reasons", []):
                if "sharpe" in reason.lower():
                    reason_counts["low_sharpe"] += 1
                elif "fitness" in reason.lower():
                    reason_counts["low_fitness"] += 1
                elif "turnover" in reason.lower():
                    reason_counts["high_turnover"] += 1
                elif "sub-universe" in reason.lower():
                    reason_counts["low_sub_universe"] += 1
                elif "ladder" in reason.lower():
                    reason_counts["low_ladder_sharpe"] += 1

        # 计算平均值
        avg_sharpe = sum(sharpe_values) / len(sharpe_values) if sharpe_values else 0
        avg_fitness = sum(fitness_values) / len(fitness_values) if fitness_values else 0
        avg_turnover = sum(turnover_values) / len(turnover_values) if turnover_values else 0

        return {
            "has_failures": True,
            "failure_count": len(failures),
            "reason_distribution": dict(reason_counts),
            "avg_sharpe": avg_sharpe,
            "avg_fitness": avg_fitness,
            "avg_turnover": avg_turnover,
            "target_sharpe": self.min_sharpe,
            "target_fitness": self.min_fitness,
            "target_turnover": self.max_turnover,
        }

    def generate_feedback_prompt(self, analysis: dict, iteration: int) -> str:
        """生成反馈给LLM的prompt"""
        if not analysis.get("has_failures"):
            return ""

        prompt = f"""# Alpha质量改进 - 第{iteration}轮迭代

## 当前问题分析

上一轮生成的alpha未达到平台标准，具体问题：

### 指标表现
- **Sharpe比率**: 平均 {analysis['avg_sharpe']:.2f} (目标: ≥{analysis['target_sharpe']:.2f})
- **Fitness**: 平均 {analysis['avg_fitness']:.2f} (目标: ≥{analysis['target_fitness']:.2f})
- **Turnover**: 平均 {analysis['avg_turnover']:.2%} (目标: ≤{analysis['target_turnover']:.0%})

### 失败原因分布
"""
        for reason, count in analysis["reason_distribution"].items():
            prompt += f"- {reason}: {count}次\n"

        prompt += """
## 改进方向

请生成新的alpha表达式，重点改进以下方面：

1. **提高Sharpe比率** (当前不足):
   - 使用更强的预测信号（基本面变化、分析师创新、异常值）
   - 增加信号的时间平滑（ts_mean, ts_decay_linear）
   - 组合多个弱相关信号

2. **提高Fitness** (信息系数):
   - 选择高预测力的因子组合
   - 避免过度复杂的嵌套
   - 确保信号在横截面上有足够区分度

3. **控制Turnover** (换手率):
   - 使用较长的回看窗口（126天以上）
   - 避免高频交易信号（避免ts_delta短窗口）
   - 使用ts_decay_linear代替简单rank

4. **避免已知失败模式**:
   - 不要使用已被证明无效的因子组合
   - 避免过度依赖单一数据源
   - 确保表达式在多个子行业有效

## 要求

- 生成10个新的候选表达式
- 每个表达式要有创新性（不重复之前失败的模式）
- 优先使用：group_neutralize + ts_zscore + rank组合
- 控制复杂度：嵌套层数≤4层
- 确保语法正确（FastExpr格式）
"""
        return prompt

    def run_iteration(self, iteration: int, candidates: list[dict]) -> tuple[list[dict], dict]:
        """执行一轮迭代"""
        print(f"\n{'='*70}")
        print(f"第 {iteration}/{self.max_iterations} 轮迭代")
        print(f"{'='*70}")
        print(f"候选数量: {len(candidates)}")
        print()

        # Simulate
        results = self.simulate_batch(candidates)

        # 统计
        passed = [r for r in results if r.get("passed")]
        failed = [r for r in results if not r.get("passed")]

        stats = {
            "iteration": iteration,
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": len(passed) / len(results) if results else 0,
        }

        print(f"\n{'='*70}")
        print(f"第{iteration}轮结果")
        print(f"{'='*70}")
        print(f"总计: {stats['total']}")
        print(f"通过: {stats['passed']} ({stats['pass_rate']:.1%})")
        print(f"失败: {stats['failed']}")
        print()

        # 保存通过的
        self.passed_alphas.extend(passed)

        # 保存反馈到数据库
        self.feedback_db.extend(results)

        return results, stats

    def run(self, initial_candidates_path: Path) -> dict:
        """运行完整的迭代流程"""
        print("="*70)
        print("🚀 迭代提交Alpha - 带反馈学习")
        print("="*70)
        print(f"目标: 获得 {self.target_count} 个通过的alpha")
        print(f"标准: sharpe≥{self.min_sharpe}, fitness≥{self.min_fitness}, turnover≤{self.max_turnover:.0%}")
        print(f"最大迭代: {self.max_iterations} 轮")
        print()

        # 加载初始候选
        candidates = self.load_candidates(initial_candidates_path)
        print(f"✅ 加载了 {len(candidates)} 个初始候选")
        print()

        for iteration in range(1, self.max_iterations + 1):
            # 执行simulate
            results, stats = self.run_iteration(iteration, candidates)
            self.iteration_stats.append(stats)

            # 检查是否达到目标
            if len(self.passed_alphas) >= self.target_count:
                print(f"\n🎉 已达到目标！获得 {len(self.passed_alphas)} 个通过的alpha")
                break

            # 如果还没达到目标，且不是最后一轮，生成新候选
            if iteration < self.max_iterations:
                print(f"\n当前通过数: {len(self.passed_alphas)}/{self.target_count}")
                print("分析失败原因，准备生成新候选...")

                # 分析失败原因
                analysis = self.analyze_failures(results)

                if analysis.get("has_failures"):
                    # 生成反馈prompt
                    feedback_prompt = self.generate_feedback_prompt(analysis, iteration + 1)

                    # 保存反馈prompt到文件，供用户查看
                    feedback_file = _ROOT / f"feedback_prompt_iteration_{iteration+1}.txt"
                    feedback_file.write_text(feedback_prompt, encoding="utf-8")
                    print(f"✅ 反馈prompt已保存到: {feedback_file}")
                    print()
                    print("⚠️ 需要手动运行LLM生成新候选，或者使用生成高质量Alpha.py")
                    print("提示：将反馈prompt发送给LLM，让它生成改进的表达式")
                    print()

                    # 这里可以集成自动调用LLM，暂时手动
                    break

        # 生成最终报告
        return self.generate_report()

    def generate_report(self) -> dict:
        """生成最终报告"""
        report_path = _ROOT / "迭代提交报告.md"

        with report_path.open("w", encoding="utf-8") as f:
            f.write("# 迭代提交Alpha - 最终报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")

            f.write("## 📊 总体统计\n\n")
            f.write(f"- **执行轮数**: {len(self.iteration_stats)}\n")
            f.write(f"- **通过数量**: {len(self.passed_alphas)}\n")
            f.write(f"- **反馈数据**: {len(self.feedback_db)} 条\n\n")

            if self.iteration_stats:
                f.write("## 📈 各轮迭代统计\n\n")
                f.write("| 轮次 | 总计 | 通过 | 失败 | 通过率 |\n")
                f.write("|------|------|------|------|--------|\n")
                for stat in self.iteration_stats:
                    f.write(f"| {stat['iteration']} | {stat['total']} | {stat['passed']} | {stat['failed']} | {stat['pass_rate']:.1%} |\n")
                f.write("\n")

            if self.passed_alphas:
                f.write("## ✅ 通过的Alpha\n\n")
                f.write("| Alpha ID | Sharpe | Fitness | Turnover |\n")
                f.write("|----------|--------|---------|----------|\n")
                for alpha in self.passed_alphas[:20]:
                    f.write(f"| {alpha['alpha_id']} | {alpha['sharpe']:.2f} | {alpha['fitness']:.2f} | {alpha['turnover']:.2%} |\n")
                if len(self.passed_alphas) > 20:
                    f.write(f"\n... 还有 {len(self.passed_alphas) - 20} 个\n")
                f.write("\n")

            f.write("## 📋 反馈数据库\n\n")
            f.write(f"反馈数据已保存，可用于后续训练改进。\n")
            f.write(f"总计: {len(self.feedback_db)} 条记录\n\n")

        print(f"✅ 报告已保存到: {report_path}")

        return {
            "passed_count": len(self.passed_alphas),
            "iterations": len(self.iteration_stats),
            "feedback_count": len(self.feedback_db),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="迭代提交Alpha - 带反馈学习")
    parser.add_argument("--input", type=Path, default=Path("高质量Alpha候选.csv"), help="输入候选CSV")
    parser.add_argument("--max-iterations", type=int, default=5, help="最大迭代轮数")
    parser.add_argument("--target-count", type=int, default=10, help="目标通过数量")
    parser.add_argument("--min-sharpe", type=float, default=1.58, help="最低Sharpe要求")
    parser.add_argument("--min-fitness", type=float, default=1.0, help="最低Fitness要求")
    parser.add_argument("--max-turnover", type=float, default=0.7, help="最高Turnover要求")

    args = parser.parse_args()

    submitter = IterativeSubmitter(
        min_sharpe=args.min_sharpe,
        min_fitness=args.min_fitness,
        max_turnover=args.max_turnover,
        max_iterations=args.max_iterations,
        target_count=args.target_count,
    )

    result = submitter.run(args.input)

    print()
    print("="*70)
    print("✅ 迭代完成")
    print("="*70)
    print(f"通过: {result['passed_count']}")
    print(f"迭代轮数: {result['iterations']}")
    print(f"反馈数据: {result['feedback_count']} 条")
    print()

    return 0 if result["passed_count"] >= args.target_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
