#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量替换 auto_alpha_pipeline_optimized_final_updated_fixed.py 的核心生成逻辑，
从 group_rank 同质化模板转向 Alpha Models.csv 中验证的高分模板族。
"""
import sys
from pathlib import Path

FILE = Path(r"C:\Users\Windows11\Desktop\alpha\auto_alpha_pipeline_optimized_final_updated_fixed.py")
assert FILE.is_file(), f"目标文件不存在: {FILE}"

lines = FILE.read_text(encoding="utf-8").splitlines()

# ------------------------------------------------------------
# 1. 定位关键行号
# ------------------------------------------------------------
start_line = None   # _emit_legacy_cartesian_family() 调用
end_line = None     # # Candidate generation 注释
for i, line in enumerate(lines):
    if line.strip() == "_emit_legacy_cartesian_family()":
        start_line = i
    if "# Candidate generation: more recall than the over-narrow version, but still controlled." in line:
        end_line = i

assert start_line is not None, "未找到 _emit_legacy_cartesian_family() 调用"
assert end_line is not None, "未找到 # Candidate generation 注释"
print(f"[定位] hypotheses 列表: 行 {start_line + 1} ~ {end_line + 1}")

# ------------------------------------------------------------
# 2. 构造新的 hypotheses 列表
# ------------------------------------------------------------
NEW_HYPOTHESES = '''        _emit_legacy_cartesian_family()

        hypotheses: list[dict[str, Any]] = []

        # ============================================================
        # 核心高分模板族（基于 Alpha Models.csv 验证逻辑）
        # 目标：抛弃 group_rank(f/cap, group)-0.5 同质化陷阱，
        # 转向 regression_neut / group_neutralize / ts_zscore / bucket 分层 / 多因子组合
        # ============================================================

        # -- A. zscore + 行业中性化（高通过率基础族）
        zscore_pool = self._limited_round_robin([theme_fields.get("profitability", []), theme_fields.get("quality", []), fundamental, other], 30)
        if zscore_pool:
            hypotheses.append(
                {
                    "id": "zscore_neutralize",
                    "factor_class": "neutral",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "defensive",
                    "horizon": "long",
                    "turnover": "low",
                    "priority": 96.0,
                    "pool": zscore_pool,
                    "mode": "single",
                    "templates": [
                        ("zscore_neut_sub_252", "group_neutralize(ts_zscore({field}, 252), subindustry)"),
                        ("zscore_neut_ind_252", "group_neutralize(ts_zscore({field}, 252), industry)"),
                        ("zscore_neut_sec_126", "group_neutralize(ts_zscore({field}, 126), sector)"),
                        ("zscore_neut_sub_126", "group_neutralize(ts_zscore({field}, 126), subindustry)"),
                        ("zscore_neut_ind_63",  "group_neutralize(ts_zscore({field}, 63), industry)"),
                    ],
                }
            )

        # -- B. bucket(cap分层) + 中性化（避免大小盘偏差）
        bucket_pool = self._limited_round_robin([fundamental, other], 24)
        if bucket_pool:
            hypotheses.append(
                {
                    "id": "bucket_neutralize",
                    "factor_class": "bucket",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "defensive",
                    "horizon": "mid",
                    "turnover": "low",
                    "priority": 94.0,
                    "pool": bucket_pool,
                    "mode": "single",
                    "templates": [
                        ("bucket_rank_cap", 'group_neutralize(rank({field}/cap), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("bucket_zscore", 'group_neutralize(ts_zscore({field}, 252), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("bucket_rank_mean_cap", 'group_neutralize(rank({field}/ts_mean(cap, 63)), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("bucket_sub_ind", 'group_neutralize(rank({field}/cap), bucket(rank(cap), range="0.2,1,0.2"))'),
                    ],
                }
            )

        # -- C. 回归残差族（去除共同因子后提取纯 alpha）
        reg_pool = self._limited_round_robin([fundamental, analyst, model, other], 22)
        if len(reg_pool) >= 2:
            hypotheses.append(
                {
                    "id": "regression_residual",
                    "factor_class": "regression",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "neutral",
                    "horizon": "long",
                    "turnover": "low",
                    "priority": 93.0,
                    "pool": reg_pool,
                    "mode": "pair",
                    "templates": [
                        ("reg_simple", "regression_neut({f1}, {f2})"),
                        ("reg_double", "regression_neut(regression_neut({f1}, {f2}), cap)"),
                        ("reg_vect", "vector_neut(regression_neut({f1}, {f2}), ts_median(cap, 126))"),
                        ("reg_resid_std", "regression_neut({f1}, {f2}) / ts_std_dev(regression_neut({f1}, {f2}), 500)"),
                    ],
                    "pair_cap": min(18, pair_limit),
                }
            )

        # -- D. 多因子 zscore 组合（盈利/成长/质量组合，CSV 模板 35-38）
        composite_pool = self._limited_round_robin([
            theme_fields.get("profitability", []),
            theme_fields.get("growth", []),
            theme_fields.get("quality", []),
            fundamental, other
        ], 24)
        if len(composite_pool) >= 3:
            hypotheses.append(
                {
                    "id": "composite_zscore",
                    "factor_class": "composite",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "defensive",
                    "horizon": "long",
                    "turnover": "low",
                    "priority": 95.0,
                    "pool": composite_pool,
                    "mode": "triple",
                    "templates": [
                        ("composite_sec", 'group_zscore({f1}, sector) + group_zscore({f2}, sector) - group_zscore({f3}, sector)'),
                        ("composite_ind", 'group_zscore({f1}, industry) + group_zscore({f2}, industry) - group_zscore({f3}, industry)'),
                        ("composite_sub", 'group_zscore({f1}, subindustry) + group_zscore({f2}, subindustry) - group_zscore({f3}, subindustry)'),
                    ],
                    "pair_cap": min(14, pair_limit),
                }
            )

        # -- E. 长窗回归族（500日，CSV 模板 42-45）
        long_reg_pool = self._limited_round_robin([fundamental, analyst, model, other], 18)
        if len(long_reg_pool) >= 2:
            hypotheses.append(
                {
                    "id": "long_regression",
                    "factor_class": "regression",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "neutral",
                    "horizon": "long",
                    "turnover": "low",
                    "priority": 90.0,
                    "pool": long_reg_pool,
                    "mode": "pair",
                    "templates": [
                        ("long_reg_500", "ts_regression(ts_zscore({f1}, 500), ts_zscore({f2}, 500), 500)"),
                        ("long_reg_time", "ts_regression(ts_zscore({f1}, 500), timestep(500), 500)"),
                        ("long_reg_resid_inv", 'residual = ts_regression(ts_zscore({f1}, 500), ts_zscore({f2}, 500), 500); 1 / ts_std_dev(residual, 500)'),
                        ("long_reg_cross", 'regression_neut(group_neutralize(ts_zscore({f1}, 252), bucket(rank(cap), range="0.1,1,0.1")), ts_regression(ts_zscore({f2}, 252), timestep(252), 252))'),
                    ],
                    "pair_cap": min(12, pair_limit),
                }
            )

        # -- F. 截面乘法族（非线性组合，CSV 模板 33）
        multif_pool = self._limited_round_robin([fundamental, analyst, model, other], 20)
        if len(multif_pool) >= 3:
            hypotheses.append(
                {
                    "id": "multiplicative_rank",
                    "factor_class": "multiplicative",
                    "field_domain": "mixed",
                    "family_group": "diverse",
                    "regime": "neutral",
                    "horizon": "mid",
                    "turnover": "mid",
                    "priority": 88.0,
                    "pool": multif_pool,
                    "mode": "triple",
                    "templates": [
                        ("multif_bucket", 'group_neutralize(rank({f1}) * rank({f2}) * rank({f3}), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("multif_pure", "rank({f1}) * rank({f2}) * rank({f3})"),
                    ],
                    "pair_cap": min(12, pair_limit),
                }
            )

        # -- G. 衰减+量能修正（CSV 模板 34）
        decay_pool = self._limited_round_robin([fundamental, pv], 16)
        if decay_pool:
            hypotheses.append(
                {
                    "id": "decay_volume",
                    "factor_class": "decay",
                    "field_domain": "mixed",
                    "family_group": "diverse",
                    "regime": "defensive",
                    "horizon": "mid",
                    "turnover": "low",
                    "priority": 86.0,
                    "pool": decay_pool,
                    "mode": "single",
                    "templates": [
                        ("decay_vol", 'group_neutralize(ts_decay_linear({field}, 20) / ts_sum(volume, 252), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("decay_mean_vol", 'group_neutralize(ts_decay_linear({field}, 40) / ts_mean(volume, 252), bucket(rank(cap), range="0.1,1,0.1"))'),
                    ],
                }
            )

        # -- H. 相关性反转 / 量价背离（CSV 模板 12-13）
        pv_fields_for_corr = _top_unique(self._limited_round_robin([pv, ["returns"], ["volume"]], 10), 10)
        if pv_fields_for_corr:
            hypotheses.append(
                {
                    "id": "correlation_reversal",
                    "factor_class": "reversal",
                    "field_domain": "pv",
                    "family_group": "diverse",
                    "regime": "stress",
                    "horizon": "short",
                    "turnover": "mid",
                    "priority": 84.0,
                    "pool": pv_fields_for_corr,
                    "mode": "single",
                    "templates": [
                        ("corr_rev", 'group_neutralize(-ts_corr(volume, abs(returns), 20), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("price_vol_div", 'group_neutralize(ts_corr(ts_delay(close, 1), volume, 10) * -ts_delta(close, 5), bucket(rank(cap), range="0.1,1,0.1"))'),
                    ],
                }
            )

        # -- I. 估值反转（CSV 模板 39）
        value_pool = self._limited_round_robin([fundamental, other], 20)
        if value_pool:
            hypotheses.append(
                {
                    "id": "value_reversal",
                    "factor_class": "value",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "defensive",
                    "horizon": "long",
                    "turnover": "low",
                    "priority": 85.0,
                    "pool": value_pool,
                    "mode": "single",
                    "templates": [
                        ("val_rev_sub", 'group_rank(1 / ({field} + 1e-6), subindustry) - 0.5'),
                        ("val_rev_ind", 'group_rank(1 / ({field} + 1e-6), industry) - 0.5'),
                    ],
                }
            )

        # -- J. 条件交易（CSV 模板 32）
        event_pool = self._limited_round_robin([sent_alt, analyst, model, fundamental], 18)
        if event_pool:
            hypotheses.append(
                {
                    "id": "conditional_trade",
                    "factor_class": "event",
                    "field_domain": "mixed",
                    "family_group": "diverse",
                    "regime": "opportunistic",
                    "horizon": "mid",
                    "turnover": "mid",
                    "priority": 82.0,
                    "pool": event_pool,
                    "mode": "single",
                    "templates": [
                        ("trade_when_vol", 'trade_when(ts_arg_min(volume, 5) > 3, rank({field}), -1)'),
                    ],
                }
            )

        # -- K. 分析师/价格比率 + 双中性（CSV 模板 19-20）
        analyst_price_pool = self._limited_round_robin([analyst, model], 16)
        if analyst_price_pool:
            hypotheses.append(
                {
                    "id": "analyst_price_ratio",
                    "factor_class": "analyst",
                    "field_domain": "analyst",
                    "family_group": "diverse",
                    "regime": "opportunistic",
                    "horizon": "mid",
                    "turnover": "mid",
                    "priority": 91.0,
                    "pool": analyst_price_pool,
                    "mode": "single",
                    "templates": [
                        ("analyst_cap", "vector_neut(ts_rank(vec_max({field}) / close, 120), ts_median(cap, 120))"),
                        ("analyst_dual_neut", 'group_neutralize(regression_neut(vector_neut(ts_rank(vec_max({field}) / close, 120), ts_median(cap, 120)), abs(ts_mean(returns, 252) / ts_std_dev(returns, 252))), bucket(rank(cap), range="0.1,1,0.1"))'),
                    ],
                }
            )

        # -- L. 反转 + 稳定性（CSV 模板 27-28）
        reversal_pool = self._limited_round_robin([fundamental, other], 16)
        if reversal_pool:
            hypotheses.append(
                {
                    "id": "reversal_stability",
                    "factor_class": "reversal",
                    "field_domain": "fundamental",
                    "family_group": "diverse",
                    "regime": "stress",
                    "horizon": "short",
                    "turnover": "mid",
                    "priority": 83.0,
                    "pool": reversal_pool,
                    "mode": "single",
                    "templates": [
                        ("rev_stab_sub", "group_neutralize(vector_neut(-ts_delta({field}, 3), abs(ts_mean(returns, 252) / ts_std_dev(returns, 252))), subindustry)"),
                        ("rev_stab_small", "vector_neut(-{field} * ts_std_dev({field}, 20), abs(ts_mean(returns, 252) / ts_std_dev(returns, 252)))"),
                    ],
                }
            )

        # -- M. 日内反转 / 波动率反转（CSV 模板 15-16）
        pv_rev_pool = _top_unique(self._limited_round_robin([pv, ["returns"], ["close"], ["open"]], 10), 10)
        if pv_rev_pool:
            hypotheses.append(
                {
                    "id": "intraday_reversal",
                    "factor_class": "reversal",
                    "field_domain": "pv",
                    "family_group": "diverse",
                    "regime": "stress",
                    "horizon": "short",
                    "turnover": "mid",
                    "priority": 81.0,
                    "pool": pv_rev_pool,
                    "mode": "single",
                    "templates": [
                        ("intraday_rev", 'group_neutralize(-ts_delta(close / open - 1, 3), bucket(rank(cap), range="0.1,1,0.1"))'),
                        ("vol_regime", 'group_neutralize(ts_std_dev({field}, 20) / ts_mean({field}, 20), industry)'),
                    ],
                }
            )

        # -- N. 新闻/情绪 + 平滑（CSV 模板 9-10）
        sent_pool = self._limited_round_robin([sent_alt, analyst], 14)
        if sent_pool:
            hypotheses.append(
                {
                    "id": "sentiment_smoothed",
                    "factor_class": "event",
                    "field_domain": "sent_alt",
                    "family_group": "diverse",
                    "regime": "opportunistic",
                    "horizon": "mid",
                    "turnover": "mid",
                    "priority": 87.0,
                    "pool": sent_pool,
                    "mode": "single",
                    "templates": [
                        ("sent_neut", "group_neutralize(ts_mean(ts_backfill({field}, 20), 20), industry)"),
                        ("sent_reg", "regression_neut(regression_neut(ts_mean(ts_backfill({field}, 20), 20), returns), ts_ir(returns, 252))"),
                    ],
                }
            )

        # -- O. 极低配额的 baseline group_rank（保留少量作为对比基线，但不再主导）
        baseline_pool = self._limited_round_robin([fundamental, other], 12)
        if baseline_pool:
            hypotheses.append(
                {
                    "id": "baseline_peer_rank",
                    "factor_class": "broad",
                    "field_domain": "fundamental",
                    "family_group": "baseline",
                    "regime": "neutral",
                    "horizon": "mid",
                    "turnover": "low",
                    "priority": 50.0,
                    "pool": baseline_pool,
                    "mode": "single",
                    "templates": [
                        ("peer_ind", "group_rank({field}/cap, industry)-0.5"),
                        ("peer_sec", "group_rank({field}/cap, sector)-0.5"),
                    ],
                }
            )

        # -- P. 配对价差（保留少量）
        pair_pool_small = self._limited_round_robin([fundamental, analyst, model], 10)
        if len(pair_pool_small) >= 2:
            hypotheses.append(
                {
                    "id": "pair_spread_minimal",
                    "factor_class": "pair",
                    "field_domain": "fundamental",
                    "family_group": "baseline",
                    "regime": "neutral",
                    "horizon": "mid",
                    "turnover": "mid",
                    "priority": 48.0,
                    "pool": pair_pool_small,
                    "mode": "pair",
                    "templates": [
                        ("pair_ind", "group_rank(({f1}-{f2})/cap, industry)-0.5"),
                    ],
                    "pair_cap": min(8, pair_limit),
                }
            )
'''

# 替换行
new_lines = lines[:start_line] + NEW_HYPOTHESES.splitlines() + lines[end_line:]

# ------------------------------------------------------------
# 3. 替换 class_caps / regime_caps / horizon_caps / family_group_caps
# ------------------------------------------------------------
content = "\n".join(new_lines)

# 定位 class_caps 块
class_caps_start = content.find('        class_caps = {')
class_caps_end = content.find('        field_cap = max(4, budget // 16)')
assert class_caps_start != -1 and class_caps_end != -1

NEW_CAPS = '''        class_caps = {
            "neutral": max(20, budget // 3),
            "bucket": max(16, budget // 4),
            "regression": max(18, budget // 3),
            "composite": max(16, budget // 4),
            "multiplicative": max(12, budget // 6),
            "decay": max(10, budget // 8),
            "reversal": max(10, budget // 8),
            "value": max(10, budget // 8),
            "event": max(8, budget // 10),
            "analyst": max(10, budget // 8),
            "broad": max(6, budget // 12),
            "pair": max(6, budget // 14),
            "baseline": max(6, budget // 14),
            "legacy_cartesian": max(2, budget // 30),
            "quality": max(4, budget // 20),
            "momentum": max(4, budget // 20),
            "liquidity": max(4, budget // 20),
            "volatility": max(4, budget // 20),
        }
        regime_caps = {
            "defensive": max(28, budget // 2),
            "neutral": max(16, budget // 3),
            "opportunistic": max(12, budget // 5),
            "stress": max(10, budget // 5),
        }
        horizon_caps = {
            "long": max(24, budget // 2),
            "mid": max(14, budget // 3),
            "short": max(6, budget // 8),
        }
        family_group_caps = {
            "diverse": max(60, budget // 1),
            "baseline": max(8, budget // 10),
            "pair": max(6, budget // 14),
            "pv": max(4, budget // 20),
            "legacy": max(2, budget // 30),
        }
        field_cap = max(4, budget // 16)
'''

content = content[:class_caps_start] + NEW_CAPS + content[class_caps_end + len('        field_cap = max(4, budget // 16)'):]

# ------------------------------------------------------------
# 4. 替换 template_bonus_map
# ------------------------------------------------------------
tb_start = content.find('        template_bonus_map = {')
tb_end_marker = '        def _candidate_score(c: dict[str, Any]) -> float:'
tb_end = content.find(tb_end_marker)
assert tb_start != -1 and tb_end != -1

NEW_BONUS = '''        template_bonus_map = {
            # zscore_neutralize
            "zscore_neut_sub_252": 5.0,
            "zscore_neut_ind_252": 4.8,
            "zscore_neut_sec_126": 4.5,
            "zscore_neut_sub_126": 4.5,
            "zscore_neut_ind_63":  4.2,
            # bucket_neutralize
            "bucket_rank_cap": 4.8,
            "bucket_zscore": 4.6,
            "bucket_rank_mean_cap": 4.4,
            "bucket_sub_ind": 4.2,
            # regression_residual
            "reg_simple": 4.8,
            "reg_double": 4.6,
            "reg_vect": 4.5,
            "reg_resid_std": 4.4,
            # composite_zscore
            "composite_sec": 5.0,
            "composite_ind": 4.8,
            "composite_sub": 4.6,
            # long_regression
            "long_reg_500": 4.5,
            "long_reg_time": 4.3,
            "long_reg_resid_inv": 4.2,
            "long_reg_cross": 4.4,
            # multiplicative_rank
            "multif_bucket": 4.4,
            "multif_pure": 4.2,
            # decay_volume
            "decay_vol": 4.0,
            "decay_mean_vol": 3.8,
            # correlation_reversal
            "corr_rev": 4.2,
            "price_vol_div": 4.0,
            # value_reversal
            "val_rev_sub": 4.0,
            "val_rev_ind": 3.8,
            # conditional_trade
            "trade_when_vol": 3.8,
            # analyst_price_ratio
            "analyst_cap": 4.6,
            "analyst_dual_neut": 4.4,
            # reversal_stability
            "rev_stab_sub": 4.0,
            "rev_stab_small": 3.8,
            # intraday_reversal
            "intraday_rev": 3.8,
            "vol_regime": 3.6,
            # sentiment_smoothed
            "sent_neut": 4.2,
            "sent_reg": 4.0,
            # baseline (低分)
            "peer_ind": 1.5,
            "peer_sec": 1.2,
            "pair_ind": 1.0,
            # 旧标签兜底
            "legacy_cartesian": 0.5,
        }
'''

content = content[:tb_start] + NEW_BONUS + content[tb_end:]

# ------------------------------------------------------------
# 5. 替换 _candidate_score 中的 class_bonus
# ------------------------------------------------------------
cs_marker = '            class_bonus = {'
cs_start = content.find(cs_marker)
assert cs_start != -1
# 找到对应的闭合 brace
cs_end = content.find('            }.get(factor_class, 2.2)', cs_start)
assert cs_end != -1

NEW_CLASS_BONUS = '''            class_bonus = {
                "neutral": 5.0,
                "bucket": 4.8,
                "regression": 4.8,
                "composite": 4.8,
                "multiplicative": 4.4,
                "decay": 4.2,
                "reversal": 4.0,
                "value": 4.2,
                "event": 3.8,
                "analyst": 4.2,
                "broad": 2.0,
                "pair": 1.8,
                "baseline": 1.2,
                "legacy_cartesian": 0.8,
                "quality": 2.2,
                "momentum": 2.2,
                "liquidity": 2.2,
                "volatility": 2.2,
            }.get(factor_class, 2.0)'''

content = content[:cs_start] + NEW_CLASS_BONUS + content[cs_end + len('            }.get(factor_class, 2.2)'):]

# ------------------------------------------------------------
# 6. 替换 family_bonus
# ------------------------------------------------------------
fb_marker = '            family_bonus = {'
fb_start = content.find(fb_marker)
assert fb_start != -1
fb_end = content.find('}.get(family_group, 2.0)', fb_start)
assert fb_end != -1

NEW_FAMILY_BONUS = '''            family_bonus = {
                "diverse": 4.5,
                "baseline": 1.5,
                "pair": 1.4,
                "pv": 1.2,
                "legacy": 0.8,
            }.get(family_group, 2.0)'''

content = content[:fb_start] + NEW_FAMILY_BONUS + content[fb_end + len('            }.get(family_group, 2.0)'):]

# ------------------------------------------------------------
# 7. 替换 mutate_from_top_expressions
# ------------------------------------------------------------
mut_start_marker = '    @staticmethod\n    def mutate_from_top_expressions(top_expressions: list[str]) -> list[str]:'
mut_start = content.find(mut_start_marker)
assert mut_start != -1

# 找到该方法的结束（下一个方法或类级别定义）
mut_end_marker = '    def _filter_expressions_for_novelty('
mut_end = content.find(mut_end_marker, mut_start)
assert mut_end != -1

NEW_MUTATE = '''    @staticmethod
    def mutate_from_top_expressions(top_expressions: list[str]) -> list[str]:
        """基于 top 表达式做多样化变异，覆盖高分模板族的关键变换。"""
        mutated = []
        for expr in top_expressions:
            low = expr.lower()

            # 0) 正负号翻转
            if expr.startswith("-"):
                mutated.append(expr[1:])
                mutated.append(f"-({expr})")
            else:
                mutated.append(f"-{expr}")
                mutated.append(f"-({expr})")

            # 1) group_neutralize 分组轮换
            if "group_neutralize" in low:
                for grp in ("subindustry", "industry", "sector"):
                    if grp not in low:
                        candidate = re.sub(
                            r'(group_neutralize\\([^,]+,\\s*)\\w+',
                            rf'\\g<1>{grp}',
                            expr,
                            count=1,
                        )
                        if candidate != expr:
                            mutated.append(candidate)

            # 2) ts_zscore 窗口替换（长窗）
            for func in ("ts_zscore", "ts_rank"):
                pattern = rf'{func}\\((.+?),\\s*(\\d+)\\)'
                m = re.search(pattern, expr)
                if m:
                    current_w = int(m.group(2))
                    for nw in (63, 126, 252, 500):
                        if nw != current_w:
                            mutated.append(
                                f"{func}({m.group(1)}, {nw}){expr[m.end():]}"
                            )
                    break

            # 3) bucket range 微调
            if 'range="0.1,1,0.1"' in expr:
                mutated.append(expr.replace('range="0.1,1,0.1"', 'range="0.2,1,0.2"'))
            if 'range="0.2,1,0.2"' in expr:
                mutated.append(expr.replace('range="0.2,1,0.2"', 'range="0.1,1,0.1"'))

            # 4) regression_neut 增加嵌套层
            if "regression_neut" in low and low.count("regression_neut") == 1:
                m = re.search(r'regression_neut\\(([^()]+),\\s*([^()]+)\\)', expr)
                if m:
                    mutated.append(f"regression_neut(regression_neut({m.group(1)}, {m.group(2)}), cap)")

            # 5) group_zscore 组合加减号翻转
            if "group_zscore" in low and "+" in expr and "-" in expr:
                mutated.append(expr.replace(" + ", " - ", 1).replace(" - ", " + ", 1))

            # 6) vector_neut / ts_median 窗口替换
            if "ts_median(cap, " in low:
                for nw in (63, 126, 252):
                    mutated.append(re.sub(r'ts_median\\(cap,\\s*\\d+\\)', f'ts_median(cap, {nw})', expr))

            # 7) ts_decay_linear 窗口替换
            if "ts_decay_linear" in low:
                m = re.search(r'ts_decay_linear\\((.+?),\\s*(\\d+)\\)', expr)
                if m:
                    for nw in (10, 20, 40, 60):
                        if nw != int(m.group(2)):
                            mutated.append(
                                f"ts_decay_linear({m.group(1)}, {nw}){expr[m.end():]}"
                            )

        dedup = list(dict.fromkeys(mutated))
        return [x for x in dedup if x and len(x) < 256]
'''

content = content[:mut_start] + NEW_MUTATE + content[mut_end:]

# ------------------------------------------------------------
# 8. 修改 main 块默认配置（提升预筛阈值、减少baseline配额）
# ------------------------------------------------------------
# 替换 min_sharpe_threshold 等配置
main_start = content.find('    config = PipelineConfig(')
main_end = content.find('    pipeline = WorldQuantAlphaPipeline(config)')
assert main_start != -1 and main_end != -1

old_main = content[main_start:main_end]

# 做局部替换
new_main = old_main.replace(
    '        min_sharpe_threshold=1.0,',
    '        min_sharpe_threshold=1.15,   # 更接近 WQ 严格线 1.25，减少低质 simulate'
).replace(
    '        min_fitness_threshold=0.80,',
    '        min_fitness_threshold=0.95,  # 更接近 WQ 严格线 1.0'
).replace(
    '        min_subuniverse_sharpe_threshold=-0.50,',
    '        min_subuniverse_sharpe_threshold=0.0,  # 子样本必须非负'
).replace(
    '        max_generated_expressions=250,',
    '        max_generated_expressions=220,  # 精减池子，突出高分族'
).replace(
    '        generation_max_per_family=180,',
    '        generation_max_per_family=120,  # 减少单族噪声'
).replace(
    '        pair_generation_limit=24,',
    '        pair_generation_limit=30,       # 高分 pair/triple 族需要更多组合'
).replace(
    '        field_top_n=200,',
    '        field_top_n=180,                # 聚焦高质量字段'
).replace(
    '        top_k_per_round=25,',
    '        top_k_per_round=20,             # 只把最优质的带入下一轮'
).replace(
    '        stop_if_no_pass_after=200,',
    '        stop_if_no_pass_after=120,    # 更快止损低质批次'
).replace(
    '        min_pass_required_to_continue=2,',
    '        min_pass_required_to_continue=1,'
).replace(
    '        rounds=2,',
    '        rounds=3,                      # 多一轮让高分模板充分变异'
)

content = content[:main_start] + new_main + content[main_end:]

# ------------------------------------------------------------
# 9. 写入文件
# ------------------------------------------------------------
FILE.write_text(content, encoding="utf-8")
print("[完成] 所有核心替换已写入文件。")
