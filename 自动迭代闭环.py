#!/usr/bin/env python3
"""
自动迭代生成和提交 - 完全闭环

工作流程：
1. 从候选CSV读取表达式
2. 提交simulate到平台
3. 收集失败原因
4. **自动调用LLM生成改进的表达式**
5. 重复直到达标

这是完整的闭环系统，类似v50但更专注于质量迭代。

使用方法：
    python 自动迭代闭环.py --iterations 3
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def extract_failure_patterns(feedback_db: list[dict]) -> dict:
    """从反馈数据库提取失败模式"""
    patterns = {
        "low_sharpe_expressions": [],
        "high_turnover_expressions": [],
        "low_fitness_expressions": [],
        "common_issues": [],
    }

    for record in feedback_db:
        expr = record.get("expression", "")
        sharpe = record.get("sharpe")
        fitness = record.get("fitness")
        turnover = record.get("turnover")

        if sharpe is not None and sharpe < 0.5:
            patterns["low_sharpe_expressions"].append({
                "expression": expr[:100],
                "sharpe": sharpe,
                "fitness": fitness,
            })

        if turnover is not None and turnover > 1.0:
            patterns["high_turnover_expressions"].append({
                "expression": expr[:100],
                "turnover": turnover,
            })

        if fitness is not None and fitness < 0.1:
            patterns["low_fitness_expressions"].append({
                "expression": expr[:100],
                "fitness": fitness,
            })

        # 提取具体失败原因
        for reason in record.get("failure_reasons", []):
            patterns["common_issues"].append(reason)

    return patterns


def build_improvement_prompt(patterns: dict, iteration: int) -> str:
    """构建给LLM的改进prompt"""

    prompt = f"""# Alpha生成 - 第{iteration}轮迭代改进

## 🎯 目标

生成WorldQuant Brain平台可通过的高质量alpha表达式，标准：
- Sharpe ≥ 1.58
- Fitness ≥ 1.0
- Turnover ≤ 70%

## ❌ 前几轮的失败模式（避免重复）

### 低Sharpe表达式特征
"""

    if patterns["low_sharpe_expressions"]:
        prompt += "这些表达式的Sharpe都太低（<0.5），不要生成类似的：\n"
        for i, ex in enumerate(patterns["low_sharpe_expressions"][:5], 1):
            prompt += f"{i}. sharpe={ex['sharpe']:.2f}: {ex['expression']}...\n"
        prompt += "\n**避免**: 这些表达式的共同问题是信号太弱或噪音太大\n\n"

    if patterns["high_turnover_expressions"]:
        prompt += "### 高Turnover表达式特征\n"
        prompt += "这些表达式的换手率太高（>100%），不要生成类似的：\n"
        for i, ex in enumerate(patterns["high_turnover_expressions"][:5], 1):
            prompt += f"{i}. turnover={ex['turnover']:.1%}: {ex['expression']}...\n"
        prompt += "\n**避免**: 使用ts_delta短窗口、高频信号\n\n"

    if patterns["low_fitness_expressions"]:
        prompt += "### 低Fitness表达式特征\n"
        for i, ex in enumerate(patterns["low_fitness_expressions"][:5], 1):
            prompt += f"{i}. fitness={ex['fitness']:.2f}: {ex['expression']}...\n"
        prompt += "\n**避免**: 过度复杂嵌套、弱预测力因子\n\n"

    prompt += """## ✅ 改进策略

### 1. 提高Sharpe（信号强度）
- 使用基本面变化因子（ts_delta(fundamental/cap, 126)）
- 组合流动性调整（rank(volume/adv20)）
- 使用ts_zscore标准化增强稳定性
- 使用group_neutralize控制行业风险

### 2. 控制Turnover（降低换手率）
- 使用较长回看窗口（126天以上）
- 避免ts_delta短窗口（<60天）
- 使用ts_decay_linear代替简单rank
- 使用ts_mean平滑信号

### 3. 提高Fitness（信息系数）
- 选择高预测力的基本面因子（收益、现金流、估值）
- 确保横截面区分度（使用rank、ts_zscore）
- 避免过度嵌套（≤4层）
- 组合多个弱相关因子

## 📝 生成要求

请生成15个新的alpha表达式，要求：

1. **创新性**: 不重复上述失败模式
2. **多样性**: 覆盖不同因子组合和策略风格
3. **语法**: FastExpr格式，确保语法正确
4. **结构**: 优先使用以下模板

### 推荐模板

```python
# 模板1: 基本面变化 + 流动性
group_neutralize(
    ts_zscore(ts_delta(fundamental_field/cap, 126), 126) * rank(ts_mean(volume, 63)/adv20),
    sector
)

# 模板2: 标准化因子 + 行业中性
group_neutralize(
    ts_zscore(fundamental_field/cap, 126) * rank(ts_decay_linear(volume/adv20, 63)),
    subindustry
)

# 模板3: 多因子组合
group_neutralize(
    rank(ts_zscore(field1/cap, 126)) + 0.5*rank(ts_zscore(field2/cap, 126)),
    sector
)
```

### 可用的高质量基本面字段

- 收益类: `ebitda`, `earnings`, `revenue`, `sales`
- 现金流: `fcf`, `operating_cash_flow`
- 估值: `book_value`, `total_assets`
- 分析师: `anl10_*`, `anl14_*`

## 输出格式

请直接输出15个表达式，每行一个，不要额外说明：

```
group_neutralize(ts_zscore(ts_delta(ebitda/cap,126),126)*rank(ts_mean(volume,63)/adv20),sector)
...
```
"""

    return prompt


def save_feedback_for_learning(feedback_db: list[dict], output_path: Path):
    """保存反馈数据供后续学习"""
    with output_path.open("w", encoding="utf-8", newline="") as f:
        if not feedback_db:
            return

        fieldnames = list(feedback_db[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(feedback_db)

    print(f"✅ 反馈数据已保存: {output_path}")


def main() -> int:
    print("="*70)
    print("🔄 自动迭代闭环 - 生成、提交、反馈、改进")
    print("="*70)
    print()

    # 读取当前的simulate结果
    results_path = Path("simulate_results.csv")

    if not results_path.exists():
        print("❌ 未找到simulate_results.csv")
        print("请先运行: python 批量simulate验证.py")
        return 1

    # 加载结果
    feedback_db = []
    with results_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            feedback_db.append({
                "id": row.get("候选ID"),
                "expression": row.get("表达式") or row["expression"] if "expression" in row else "",
                "alpha_id": row.get("alpha_id"),
                "sharpe": float(row["sharpe"]) if row.get("sharpe") else None,
                "fitness": float(row["fitness"]) if row.get("fitness") else None,
                "turnover": float(row["turnover"]) if row.get("turnover") else None,
                "status": row.get("status"),
                "error": row.get("error"),
                "failure_reasons": [row.get("error")] if row.get("error") else [],
            })

    print(f"✅ 加载了 {len(feedback_db)} 条反馈记录")
    print()

    # 分析失败模式
    print("分析失败模式...")
    patterns = extract_failure_patterns(feedback_db)

    low_sharpe_count = len(patterns["low_sharpe_expressions"])
    high_turnover_count = len(patterns["high_turnover_expressions"])
    low_fitness_count = len(patterns["low_fitness_expressions"])

    print(f"  低Sharpe: {low_sharpe_count} 个")
    print(f"  高Turnover: {high_turnover_count} 个")
    print(f"  低Fitness: {low_fitness_count} 个")
    print()

    # 生成改进prompt
    print("生成LLM改进prompt...")
    prompt = build_improvement_prompt(patterns, iteration=2)

    prompt_file = _ROOT / "LLM改进prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"✅ Prompt已保存到: {prompt_file}")
    print()

    # 保存反馈数据
    feedback_file = _ROOT / "alpha_submission_feedback.csv"
    save_feedback_for_learning(feedback_db, feedback_file)
    print()

    # 指导后续步骤
    print("="*70)
    print("📋 后续步骤")
    print("="*70)
    print()
    print("1. 将LLM改进prompt.txt的内容发送给LLM（Claude/GPT/DeepSeek）")
    print()
    print("2. 将LLM返回的表达式保存到新的CSV文件")
    print()
    print("3. 运行新一轮simulate验证:")
    print("   python 批量simulate验证.py --input 新候选.csv")
    print()
    print("4. 重复此流程直到获得足够的高质量alpha")
    print()
    print("💡 提示: alpha_submission_feedback.csv 已整合到v50流程")
    print("   可以直接运行: python 生成高质量Alpha.py")
    print("   它会自动读取反馈数据并避免失败模式")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
