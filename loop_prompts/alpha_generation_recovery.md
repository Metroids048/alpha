# Alpha Generation Recovery Loop Prompt

## 目标
在10轮循环内，将 alpha 生成链路从当前的"零入队、全拒绝"状态恢复到稳定产出（每轮入队>=1，拒绝率<50%）。

## 当前障碍（2026-08-06诊断）
1. **反馈饥饿**：feedback=0 positive=0 near_pass=0 → LLM没有历史正例可学习
2. **种子重复**：SEED_TOPOLOGY_DUPLICATE:10/22 → 45%候选撞已有拓扑
3. **字段规划错误**：PLAN_UNKNOWN_FIELD + PLAN_CROSS_DATASET → 规划阶段未对齐catalog
4. **机制-算子不匹配**：MECHANISM_OPERATOR_MISMATCH:5 → 经济机制与表达式算子逻辑冲突
5. **待提交CSV遗留**：前9行旧合同候选被REJECTED_LOCAL_REVALIDATION拦截

## 循环策略（按优先级）

### 阶段1：清理遗留 + 解锁反馈（1-2轮）
**诊断点**：`git diff` + `待提交Alpha列表.csv` + `research_memory.sqlite`

1. **清理旧合同候选**（LEGACY_CONTRACT_MISSING_EVIDENCE）
   - 检查：`SELECT candidate_id, last_error FROM candidates WHERE last_error_category='LEGACY_CONTRACT_MISSING_EVIDENCE'`
   - 操作：批量更新质量证据合同或标记为`REJECTED_OBSOLETE`，避免重新验证时卡住
   
2. **解锁PENDING_SIMULATION候选**
   - 检查：待提交CSV中`queue_status=PENDING_SIMULATION`的5行
   - 问题：平台会话过期 → simulate阻塞
   - 方案A（推荐）：恢复`.wq_auth_state.json` → 运行`提交Alpha.py --dry-run`触发simulate
   - 方案B（离线）：将这5个候选标记为`READY`跳过simulate（需明确告知用户风险）

3. **注入初始反馈种子**
   - 从`alpha_state.json`或历史snapshot提取至少1个`positive`样本（Sharpe>1.5或Fitness>0.6）
   - 插入`feedback`表：`(alpha_id, is_positive=1, sharpe, fitness, turnover, ...)`
   - 目的：让下一轮LLM有1个正例参考，打破冷启动

**预期输出**：
- 旧合同候选：9→0
- PENDING_SIMULATION：5→触发simulate或标记READY
- feedback表：0→至少1条positive记录
- 下轮生成：`feedback>=1`

---

### 阶段2：种子去重强化（3-4轮）
**诊断点**：`top_rejections=SEED_TOPOLOGY_DUPLICATE:N`

1. **分析重复模式**
   ```sql
   SELECT structure_signature, COUNT(*) as cnt 
   FROM candidates 
   WHERE created_at > date('now', '-7 days')
   GROUP BY structure_signature 
   HAVING cnt > 3 
   ORDER BY cnt DESC LIMIT 10;
   ```
   
2. **种子多样性扩展**
   - 当前种子池：3个（`seeds=3`）
   - 操作：从knowledge hub中选取不同算子家族的种子（group_neutralize → ts_rank → rank → ts_mean）
   - 添加约束：禁止连续2轮使用相同`parent_template`

3. **拓扑指纹去重前置**
   - 在LLM候选生成后、入库前，增加内存级`structure_signature`去重
   - 与库存对比窗口：最近30天（而非全量历史）

**预期输出**：
- SEED_TOPOLOGY_DUPLICATE：10→<=3
- 种子池：3→6-8个不同算子家族
- 拒绝率：25/35 (71%) → <=50%

---

### 阶段3：规划阶段字段对齐（5-6轮）
**诊断点**：`top_rejections=PLAN_UNKNOWN_FIELD:N,PLAN_CROSS_DATASET:N`

1. **catalog时效性检查**
   ```bash
   stat .alpha_operators_cache.json  # 检查更新时间
   ```
   - 如果>7天：运行`python -m alpha_mining.domain.field_catalog refresh`
   
2. **LLM prompt增强**
   - 当前：隐式传递5697字段
   - 改进：在研究计划prompt中明确列举**可用数据集**和**每个数据集的TOP50高质量字段**
   - 约束：`"CRITICAL: Only use fields from the provided catalog. Cross-dataset operations require explicit group_neutralize."`

3. **规划后置验证**
   - 在`phase2_llm_acceptance.py`中添加pre-flight check：
     ```python
     if plan_uses_unknown_field(plan, catalog):
         return REJECT("PLAN_UNKNOWN_FIELD")
     if plan_mixes_datasets_without_neutralize(plan):
         return REJECT("PLAN_CROSS_DATASET")
     ```

**预期输出**：
- PLAN_UNKNOWN_FIELD + PLAN_CROSS_DATASET：4→0
- catalog刷新：如果过期则更新
- 规划拒绝率：降低10-15%

---

### 阶段4：机制-算子一致性（7-8轮）
**诊断点**：`top_rejections=MECHANISM_OPERATOR_MISMATCH:N`

1. **分析不匹配案例**
   ```sql
   SELECT expression, economic_hypothesis, economic_rationale 
   FROM candidates 
   WHERE last_error_category='MECHANISM_OPERATOR_MISMATCH' 
   LIMIT 5;
   ```
   
2. **典型模式修复**
   - 声称"momentum"但用`ts_std_dev`（波动率算子）
   - 声称"value"但用`ts_delta`（变化率算子）
   - 声称"mean-reversion"但用单调`rank`
   
3. **LLM prompt约束**
   - 添加算子语义表：`{ts_delta: "change/momentum", rank: "cross-sectional ranking", ts_std_dev: "volatility"}`
   - Prompt模板：`"Ensure economic_hypothesis aligns with operator semantics: if claiming momentum, use ts_delta/ts_mean; if claiming volatility, use ts_std_dev."`

**预期输出**：
- MECHANISM_OPERATOR_MISMATCH：5→<=1
- 机制描述与表达式一致性：提升到>90%

---

### 阶段5：反馈闭环验证（9-10轮）
**诊断点**：`feedback=N positive=M near_pass=K enqueued=E`

1. **触发simulate批处理**
   - 如果阶段1-4积累了10+个READY候选
   - 运行：`python 提交Alpha.py --simulate-only --batch-size 10`
   - 目标：获得真实Sharpe/Fitness反馈

2. **反馈写入验证**
   ```sql
   SELECT is_positive, COUNT(*) FROM feedback GROUP BY is_positive;
   ```
   - 预期：至少2-3条positive（Sharpe>1.5）+ 5-8条near_pass（Sharpe 1.0-1.5）

3. **闭环测试**
   - 运行新一轮生成，观察：
     - `feedback=N` 是否>0
     - LLM是否引用历史positive样本（检查`feedback_refs_json`）
     - 入队数是否稳定>=1

**预期输出**：
- feedback表：1 seed → 10+ 真实反馈
- positive：0→2-3
- near_pass：0→5-8
- enqueued：持续>=1/轮

---

## 验收标准（循环成功退出条件）

满足以下**任意3项**即可退出loop：

1. ✅ **稳定入队**：连续3轮 `enqueued>=1`
2. ✅ **拒绝率降低**：`rejected/(llm_candidates+rejected) < 0.5`
3. ✅ **反馈有效**：`feedback>=3 AND positive>=1`
4. ✅ **重复率控制**：`SEED_TOPOLOGY_DUPLICATE < 20%` 总拒绝
5. ✅ **规划阶段通过**：`PLAN_UNKNOWN_FIELD + PLAN_CROSS_DATASET = 0` 连续2轮

## 失败快速退出条件

遇到以下情况立即停止loop并上报：

1. ❌ 连续5轮 `enqueued=0 AND llm_candidates=0` → LLM完全失效
2. ❌ `hard_stop=True` 出现在`factory_control`表
3. ❌ DeepSeek API连续失败3次（401/429/500）
4. ❌ catalog刷新失败且无离线缓存

## 每轮执行模板

```python
# 伪代码
for cycle in range(1, 11):
    # 1. 诊断当前状态
    metrics = parse_last_generation_log()
    csv_status = analyze_pending_csv()
    feedback_stats = query_feedback_table()
    
    # 2. 匹配当前阶段
    if cycle <= 2:
        execute_stage1_cleanup(csv_status, feedback_stats)
    elif cycle <= 4:
        execute_stage2_dedup(metrics)
    elif cycle <= 6:
        execute_stage3_catalog(metrics)
    elif cycle <= 8:
        execute_stage4_mechanism(metrics)
    else:
        execute_stage5_feedback(csv_status, feedback_stats)
    
    # 3. 运行生成
    run_generation_once()
    
    # 4. 检查退出条件
    if check_success_criteria(metrics_history):
        log("SUCCESS: 达到验收标准")
        break
    if check_failure_criteria(metrics_history):
        log("FAILURE: 触发快速退出")
        break
    
    # 5. 间隔等待（避免API限流）
    sleep(30)
```

## 输出要求

每轮完成后，记录到`loop_recovery_log.jsonl`：
```json
{
  "cycle": 3,
  "stage": "seed_dedup",
  "action": "expanded seed pool from 3 to 7",
  "before": {"enqueued": 0, "rejected": 25, "SEED_TOPOLOGY_DUPLICATE": 10},
  "after": {"enqueued": 1, "rejected": 18, "SEED_TOPOLOGY_DUPLICATE": 3},
  "decision": "continue",
  "timestamp": "2026-08-06T13:30:00Z"
}
```

## 人工介入点

如果第5轮后仍未达到验收标准，向用户提供：
1. 当前诊断报告（metrics趋势图）
2. Top-3卡点及建议方案（A/B/C选项）
3. 是否继续自动修复 or 人工接管

---

**最后更新**：2026-08-06  
**预计完成时间**：10轮 × 2分钟/轮 = 20分钟  
**风险提示**：如果平台认证无法恢复，阶段1的方案B会跳过simulate，需用户明确授权
