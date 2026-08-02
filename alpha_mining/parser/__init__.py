"""WorldQuant API JSON 解析与指标抽取（与 monolith 中 _metric_get 等互补，可逐步迁入）。

Also hosts FastPlus FASTEXPR preflight helpers under ``fastplus_gate``.
"""

from alpha_mining.parser.fastplus_gate import FastPlusGateResult, check_expression, require_fastplus

__all__ = [
    "FastPlusGateResult",
    "check_expression",
    "require_fastplus",
]
