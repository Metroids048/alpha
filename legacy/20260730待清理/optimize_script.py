#!/usr/bin/env python3
"""优化 auto_alpha_pipeline.py 脚本"""

import re

# 读取原始文件
with open('auto_alpha_pipeline.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 优化 1: 在 _is_promising_by_metrics 中添加 adjustment_applied 变量和日志
old_is_promising_code = '''        if self.cfg.enable_adaptive_thresholds and hasattr(self, '_failure_count'):
            # 根据失败次数调整阈值，逐渐降低要求
            # 使用更激进的调整步长（乘以 2）以更快达到通过标准
            adjustment = self.cfg.threshold_adjustment_step * self._failure_count * 2
            effective_min_sharpe = max(
                self.cfg.min_sharpe_threshold_floor,
                self.cfg.min_sharpe_threshold - adjustment
            )
            effective_min_fitness = max(
                self.cfg.min_fitness_threshold_floor,
                self.cfg.min_fitness_threshold - adjustment
            )

        if pd.isna(fitness) or fitness < effective_min_fitness:'''

new_is_promising_code = '''        if self.cfg.enable_adaptive_thresholds and hasattr(self, '_failure_count'):
            # 根据失败次数调整阈值，逐渐降低要求
            # 使用更激进的调整步长（乘以 2）以更快达到通过标准
            adjustment_applied = self.cfg.threshold_adjustment_step * self._failure_count * 2
            effective_min_sharpe = max(
                self.cfg.min_sharpe_threshold_floor,
                self.cfg.min_sharpe_threshold - adjustment_applied
            )
            effective_min_fitness = max(
                self.cfg.min_fitness_threshold_floor,
                self.cfg.min_fitness_threshold - adjustment_applied
            )

        # 显示当前有效的阈值（如果启用日志）
        if self.cfg.log_effective_thresholds and 'adjustment_applied' in dir() and adjustment_applied > 0:
            print(
                f"[threshold-check] 当前有效阈值：Sharpe={effective_min_sharpe:.3f} (调整 {adjustment_applied:.3f}), "
                f"Fitness={effective_min_fitness:.3f} (调整 {adjustment_applied:.3f})"
            )

        if pd.isna(fitness) or fitness < effective_min_fitness:'''

content = content.replace(old_is_promising_code, new_is_promising_code)

# 优化 2: 修改 floor 检查部分添加 enable_subuniverse_filter 控制
old_floor_check = '''        floor = self.cfg.min_subuniverse_sharpe_threshold
        if floor is not None:
            sub_s = self._extract_subuniverse_sharpe(result_json)
            if sub_s is not None and not pd.isna(sub_s) and sub_s < floor:
                return False'''

new_floor_check = '''        floor = self.cfg.min_subuniverse_sharpe_threshold
        if floor is not None and self.cfg.enable_subuniverse_filter:
            sub_s = self._extract_subuniverse_sharpe(result_json)
            if sub_s is not None and not pd.isna(sub_s) and sub_s < floor:
                return False'''

content = content.replace(old_floor_check, new_floor_check)

# 优化 3: 更新 _increment_failure_count 方法中的变量名
old_increment_code = '''        adjustment = self.cfg.threshold_adjustment_step * self._failure_count * 2
        current_sharpe = max(self.cfg.min_sharpe_threshold_floor, self.cfg.min_sharpe_threshold - adjustment)
        current_fitness = max(self.cfg.min_fitness_threshold_floor, self.cfg.min_fitness_threshold - adjustment)'''

new_increment_code = '''        adjustment_applied = self.cfg.threshold_adjustment_step * self._failure_count * 2
        current_sharpe = max(self.cfg.min_sharpe_threshold_floor, self.cfg.min_sharpe_threshold - adjustment_applied)
        current_fitness = max(self.cfg.min_fitness_threshold_floor, self.cfg.min_fitness_threshold - adjustment_applied)'''

content = content.replace(old_increment_code, new_increment_code)

# 优化 4: 在 _prune_toxic_price_expressions 中添加更多需要剔除的模式
old_prune_function = '''    @staticmethod
    def _prune_toxic_price_expressions(expressions: list[str]) -> list[str]:
        """剔除统计上极易触发高换手/负 Sharpe 的价量陷阱式（含历史脚本残留形态）。"""
        out: list[str] = []
        dropped = 0
        for e in expressions:
            low = e.lower()
            if "ts_delta(close" in low or "ts_delta(volume" in low:
                dropped += 1
                continue
            if re.search(r"rank\\(\\s*close\\s*/\\s*ts_mean\\(\\s*close\\s*,", low):
                dropped += 1
                continue
            if re.search(r"-rank\\(\\s*ts_delta\\(\\s*close", low):
                dropped += 1
                continue
            m = re.search(r"ts_rank\\(\\s*(pv\\d+[^,]*)\\s*,\\s*(\\d+)\\s*", low, re.I)
            if m and int(m.group(2)) < 88:
                dropped += 1
                continue
            out.append(e)
        if dropped:
            print(f"[alpha] 剔除价量陷阱/短窗 pv 秩 {dropped} 条，保留 {len(out)} 条")
        return out'''

new_prune_function = '''    @staticmethod
    def _prune_toxic_price_expressions(expressions: list[str]) -> list[str]:
        """剔除统计上极易触发高换手/负 Sharpe 的价量陷阱式（含历史脚本残留形态）。"""
        out: list[str] = []
        dropped = 0
        for e in expressions:
            low = e.lower()
            # 核心陷阱模式：短窗价量直接操作
            if "ts_delta(close" in low or "ts_delta(volume" in low:
                dropped += 1
                continue
            # 过度平滑模式
            if re.search(r"rank\\(\\s*close\\s*/\\s*ts_mean\\(\\s*close\\s*,", low):
                dropped += 1
                continue
            # 反向短窗动量
            if re.search(r"-rank\\(\\s*ts_delta\\(\\s*close", low):
                dropped += 1
                continue
            # 短窗 PV 百分位
            m = re.search(r"ts_rank\\(\\s*(pv\\d+[^,]*)\\s*,\\s*(\\d+)\\s*", low, re.I)
            if m and int(m.group(2)) < 88:
                dropped += 1
                continue
            # 新增：直接 close 价格比率容易失效
            if re.search(r"close\\s*/\\s*ts_mean\\s*\\(\\s*close", low):
                dropped += 1
                continue
            # 新增：过度嵌套的 ts_mean(ts_delta(...))
            if "ts_mean(ts_delta" in low:
                dropped += 1
                continue
            out.append(e)
        if dropped:
            print(f"[alpha] 剔除价量陷阱/短窗 pv 秩 {dropped} 条，保留 {len(out)} 条")
        return out'''

content = content.replace(old_prune_function, new_prune_function)

# 写入新文件
with open('auto_alpha_pipeline.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("优化完成!")
print(f"变更总结:")
print("1. 添加了 adjustment_applied 变量用于更清晰的日志记录")
print("2. 添加了 log_effective_thresholds 配置项来控制阈值日志显示")
print("3. 添加了 enable_subuniverse_filter 配置项来灵活控制子样本 Sharpe 过滤")
print("4. 在 _prune_toxic_price_expressions 中添加了更多需剔除的模式")
