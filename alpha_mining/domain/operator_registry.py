"""WorldQuant expression operator and identifier registry."""

from __future__ import annotations

FUNCTIONS = frozenset(
    {
        "abs",
        "add",
        "divide",
        "exp",
        "floor",
        "group_neutralize",
        "group_rank",
        "group_zscore",
        "if_else",
        "log",
        "max",
        "min",
        "multiply",
        "normalize",
        "pow",
        "rank",
        "regression_neut",
        "sign",
        "subtract",
        "trade_when",
        "truncate",
        "ts_corr",
        "ts_covariance",
        "ts_decay_linear",
        "ts_delta",
        "ts_max",
        "ts_mean",
        "ts_min",
        "ts_pct_change",
        "ts_rank",
        "ts_std_dev",
        "ts_sum",
        "ts_variance",
        "ts_zscore",
        "winsorize",
        "zscore",
        "bucket",
    }
)
BLOCKED_FUNCTIONS = frozenset({"if_else", "bucket"})
GROUPS = frozenset({"market", "sector", "industry", "subindustry", "country"})
BASE_VARS = frozenset(
    {"open", "close", "high", "low", "volume", "vwap", "adv20", "returns", "cap"}
)
LITERALS = frozenset({"true", "false", "nan", "inf", "range", "rettype"})


def operator_category(name: str) -> str:
    low = str(name or "").lower()
    if low.startswith("ts_"):
        return "time_series"
    if low.startswith("group_"):
        return "group"
    if low in {"rank", "zscore", "normalize", "winsorize"}:
        return "cross_sectional"
    if low in {"trade_when", "if_else"}:
        return "conditional"
    return "arithmetic"
