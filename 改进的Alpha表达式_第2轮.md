# 改进的Alpha表达式（第2轮）

**生成时间**: 2026-08-02 22:00  
**基于反馈**: 第1轮40个候选的失败模式分析  
**改进策略**: 提高Sharpe、控制Turnover、提高Fitness

---

## 核心改进点

1. **控制Turnover**: 使用252天以上回看窗口，避免ts_delta短窗口
2. **提高Sharpe**: 使用强预测信号（基本面变化、估值、现金流）
3. **提高Fitness**: 选择高预测力因子，确保横截面区分度

---

## 15个改进表达式

```python
# 1. 估值 + 流动性（长周期）
group_neutralize(ts_zscore(book_value/cap, 252) * rank(ts_decay_linear(volume/adv20, 252)), sector)

# 2. 现金流质量（行业中性）
group_neutralize(rank(ts_zscore(fcf/cap, 252)), subindustry)

# 3. 收益质量改进
group_neutralize(rank(ts_zscore(ebitda/cap, 252)) + 0.5*rank(ts_zscore(revenue/cap, 252)), sector)

# 4. 资产效率（长周期）
group_neutralize(ts_zscore(revenue/total_assets, 252) * rank(ts_decay_linear(1/turnover_volatility, 252)), sector)

# 5. 多因子基本面组合
group_neutralize(rank(ts_zscore(ebitda/cap, 252)) + rank(ts_zscore(fcf/cap, 252)) - rank(ts_zscore(debt/total_assets, 252)), sector)

# 6. 现金流增长（避免高turnover）
group_neutralize(ts_zscore(ts_mean(fcf/cap, 252) - ts_mean(fcf/cap, 504), 252), subindustry)

# 7. 估值修正（长周期平滑）
group_neutralize(rank(ts_zscore(book_value/cap, 252)) * rank(ts_decay_linear(volume, 252)), sector)

# 8. 收益稳定性
group_neutralize(ts_zscore(ebitda/cap, 252) / ts_stddev(ebitda/cap, 252), sector)

# 9. 资产回报率质量
group_neutralize(rank(ts_zscore(earnings/total_assets, 252)) * rank(ts_decay_linear(1/adv20, 252)), subindustry)

# 10. 现金流 + 债务质量
group_neutralize(rank(ts_zscore(operating_cash_flow/cap, 252)) - 0.3*rank(ts_zscore(debt/total_assets, 252)), sector)

# 11. 收入质量（长周期）
group_neutralize(ts_zscore(sales/cap, 252) * rank(ts_decay_linear(volume/adv20, 252)), sector)

# 12. 多维度估值
group_neutralize(rank(ts_zscore(book_value/cap, 252)) + rank(ts_zscore(ebitda/cap, 252)) + rank(ts_zscore(fcf/cap, 252)), sector)

# 13. 资产效率改进
group_neutralize(rank(ts_zscore(revenue/total_assets, 252)) + rank(ts_zscore(ebitda/total_assets, 252)), subindustry)

# 14. 现金流趋势（长周期）
group_neutralize(ts_zscore(ts_decay_linear(fcf/cap, 252), 252), sector)

# 15. 综合基本面质量
group_neutralize(rank(ts_zscore(ebitda/cap, 252)) + rank(ts_zscore(fcf/cap, 252)) + rank(ts_zscore(book_value/cap, 252)) - rank(ts_zscore(debt/cap, 252)), sector)
```

---

## 与第1轮的关键差异

| 维度 | 第1轮（失败） | 第2轮（改进） |
|------|-------------|-------------|
| **回看窗口** | 126天 | 252天以上 |
| **Turnover控制** | 使用ts_delta导致170%+ | 使用ts_mean/ts_decay_linear |
| **信号强度** | 单因子，弱预测力 | 多因子组合，强预测力 |
| **因子选择** | 随机组合 | 基本面核心因子（估值/现金流/收益） |
| **标准化** | 部分缺失 | 全部使用ts_zscore标准化 |
| **行业中性** | 部分缺失 | 全部使用group_neutralize |

---

## 预期效果

基于失败模式分析，这15个表达式应该能够：

1. ✅ **Sharpe提升**: 从0.25 → 1.0+（通过多因子组合和强信号）
2. ✅ **Turnover降低**: 从173% → 70%以下（通过长周期窗口）
3. ✅ **Fitness提升**: 从0.08 → 0.5+（通过高预测力因子）

---

## 下一步操作

1. 将这15个表达式保存到 `高质量Alpha候选.csv`（覆盖或追加）
2. 运行验证: `python 批量simulate验证.py --limit 15`
3. 分析结果，如果仍未达标，继续迭代

---

**生成依据**: 基于40条反馈数据的失败模式分析  
**理论支撑**: WorldQuant论坛142篇Alpha灵感帖  
**参考文献**: 见`World quant/优质Alpha挖掘：AI工作流优化方法.md`
