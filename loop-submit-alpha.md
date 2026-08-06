# Loop Prompt: Alpha提交与质量迭代闭环

## 目标
打通完整的Alpha生成→提交→平台反馈→质量迭代闭环，确保生成的Alpha质量足以通过WorldQuant Brain平台审核。

## 循环任务

### 阶段1: 生成高质量Alpha候选
1. 运行生成流程：`python 生成Alpha.py --candidates 5`
2. 检查生成质量：
   - 读取 `待提交Alpha列表.csv`
   - 确认候选数量 > 0
   - 确认local_quality_score >= 75.0
   - 确认熔断过滤生效（查看stderr日志）

### 阶段2: 提交到平台
1. 确认平台认证有效（由用户提供扫脸认证）
2. 运行提交流程：`python 提交Alpha.py`
3. 监控提交状态：
   - 提交成功：记录alpha_id
   - 提交失败：记录错误信息

### 阶段3: 获取平台反馈
1. 等待平台simulate完成（通常需要几分钟到几小时）
2. 查询alpha状态和评估结果
3. 解析平台反馈的关键指标：
   - Sharpe ratio（目标 >= 1.58）
   - Fitness（目标 >= 1.0）
   - Turnover（目标 <= 70%）
   - Weight concentration（目标 <= 10%）
   - Sub-universe Sharpe（目标 >= -0.36）
   - IS ladder Sharpe（目标 >= 1.58）

### 阶段4: 反馈驱动迭代
1. **成功案例**：
   - 提取成功的表达式、字段组合、算子拓扑
   - 写入 `项目知识库/已验证解决方案.md`
   - 标记为positive feedback供下轮生成参考

2. **失败案例**：
   - 解析具体失败原因（如"Sharpe of -0.82 is below cutoff of 1.58"）
   - 分类失败模式：
     - LOW_SHARPE: Sharpe < 1.58
     - LOW_FITNESS: Fitness < 1.0
     - HIGH_TURNOVER: Turnover > 70%
     - CONCENTRATED_WEIGHT: Weight concentration > 10%
   - 写入feedback数据库供熔断预测器和生成器学习
   - 更新生成器的约束条件

### 阶段5: 质量提升策略
基于平台反馈调整生成策略：

**针对Sharpe过低：**
- 增加经济机制的深度要求
- 避免使用历史上Sharpe<0的字段组合
- 增强anti-correlation设计

**针对Fitness过低：**
- 增加字段质量权重（coverage、date_coverage）
- 避免过度复杂的算子嵌套
- 使用更稳定的时间序列窗口（>=63）

**针对Turnover过高：**
- 增加ts_mean等平滑算子的使用
- 增大时间窗口参数（>=126）
- 减少ts_delta等高频信号算子

**针对Weight concentration：**
- 增加group_neutralize的使用
- 避免单一行业/市值集中的字段
- 使用更分散的universe（如TOP3000）

### 阶段6: 循环条件
- **成功退出**：累计3个Alpha成功通过平台审核（Sharpe>=1.58, Fitness>=1.0）
- **继续循环**：当前轮次有失败或质量不足，基于反馈继续迭代
- **最大轮次**：20轮（防止无限循环）

## 检查点

每轮循环后检查：
- [ ] 生成的Alpha数量 > 0
- [ ] 提交流程无错误
- [ ] 成功获取平台反馈
- [ ] 反馈已写入知识库/数据库
- [ ] 下一轮生成策略已调整

## 监控指标

追踪改进趋势：
- 生成质量分布（local_quality_score）
- 提交成功率
- 平台通过率
- Sharpe/Fitness均值演化
- 熔断拦截准确率

## 实现要点

1. **平台认证持久化**：确保cookie/session在循环中保持有效
2. **反馈延迟处理**：platform simulate需要时间，需要轮询或webhook
3. **数据完整性**：每次反馈都要完整记录到sqlite和csv
4. **知识累积**：成功案例和失败模式要持久化，供后续会话使用
5. **熔断预测器更新**：根据平台真实反馈更新熔断规则

## 关键文件

- 生成：`alpha_mining/generation/high_quality.py`
- 提交：`alpha_mining/submitter/`
- 平台交互：`alpha_mining/platform/`
- 反馈存储：`candidate.db`, `待提交Alpha列表.csv`
- 知识库：`项目知识库/已验证解决方案.md`

## 下一步

在新对话中执行此loop prompt，确保：
1. 用户提供平台认证（扫脸）
2. 执行完整的生成→提交→反馈循环
3. 基于真实平台反馈迭代优化
4. 最终产出通过平台审核的高质量Alpha
