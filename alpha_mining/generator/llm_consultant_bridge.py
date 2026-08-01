"""Bridge between LLM ExpressionGenerator and ConsultantGenerator interface.

NOTE: This bridge does NOT use ExpressionGenerator because it requires
database write access (which causes locking issues). Instead, it directly
calls the LLM to generate expressions without persisting to the database.

The expressions will be persisted later during the submission phase.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha_mining.generator.consultant_generator import ConsultantCandidate


class LLMConsultantBridge:
    """
    Direct LLM generation without database persistence.

    This avoids database locking issues by not using ExpressionGenerator
    (which writes to the database). Instead, we call the LLM directly
    and return the expressions without persisting them.
    """

    def __init__(
        self,
        *,
        database: str | Path,
        llm: Any,
        max_per_hypothesis: int = 8,
    ) -> None:
        self.database = Path(database)
        self.llm = llm
        self.max_per_hypothesis = max_per_hypothesis

    def generate(
        self,
        *,
        hypothesis_id: str,
        family: str,
        mechanism: str,
        horizon: str,
        fields: tuple[str, ...],
        parent_expression: str = "",
    ) -> list[ConsultantCandidate]:
        """
        Generate candidates using direct LLM calls (no database writes).
        """
        import hashlib
        import re

        try:
            # 构建LLM提示
            fields_str = ", ".join(fields[:5])  # 限制字段数避免上下文过长
            prompt = f"""Generate {self.max_per_hypothesis} WorldQuant Brain alpha expressions.

Research hypothesis: {mechanism}
Family: {family}
Horizon: {horizon}
Available fields: {fields_str}

Requirements:
1. Use rank() wrapper for all expressions
2. Use time-series operators: ts_delta, ts_rank, ts_mean, ts_std_dev, ts_zscore, ts_decay_linear
3. Window parameters: 5, 10, 20, 63, 126
4. Each expression must be unique
5. Combine fields and operators creatively

Return ONLY valid expressions, one per line, no explanations."""

            # 调用LLM
            response = self.llm.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)

            # 提取表达式（每行一个）
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            expressions = []
            for line in lines:
                # 清理markdown代码块标记
                line = re.sub(r'^```.*$', '', line).strip()
                if line and ('rank(' in line or 'ts_' in line):
                    expressions.append(line)

            # 转换为ConsultantCandidate格式
            candidates = []
            for i, expr in enumerate(expressions[:self.max_per_hypothesis]):
                cid = hashlib.sha256(f"{hypothesis_id}_{expr}".encode()).hexdigest()[:24]
                candidates.append(ConsultantCandidate(
                    candidate_id=f"llm_{cid}",
                    expression=expr,
                    mutation_type="llm_generated",
                ))

            print(f"[llm_bridge] ✓ 生成 {len(candidates)} 个LLM候选")
            return candidates

        except Exception as exc:
            print(f"[llm_bridge] ✗ LLM生成失败 {hypothesis_id}: {exc}")
            # 回退到模板生成器
            try:
                from alpha_mining.generator.consultant_generator import ConsultantGenerator
                generator = ConsultantGenerator(
                    max_per_hypothesis=self.max_per_hypothesis,
                    max_same_behavior=2,
                )
                return generator.generate(
                    hypothesis_id=hypothesis_id,
                    family=family,
                    mechanism=mechanism,
                    horizon=horizon,
                    fields=fields,
                    parent_expression=parent_expression,
                )
            except Exception as fallback_exc:
                print(f"[llm_bridge] ✗ 回退生成器也失败: {fallback_exc}")
                return []
