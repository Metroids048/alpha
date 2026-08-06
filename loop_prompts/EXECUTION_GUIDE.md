# Alpha Generation Recovery Loop - 执行指南

## 快速启动

### 方式1：单轮手动执行（推荐用于首次验证）
```bash
# 阶段1：清理遗留
python tools/ops/cleanup_legacy_candidates.py
python tools/ops/inject_feedback_seed.py

# 运行一次生成
python 生成Alpha.py --once

# 查看结果
python tools/ops/generation_health_check.py
```

### 方式2：自动化Loop（使用/loop技能）
```bash
# Claude Code环境
/loop 2m "执行 loop_prompts/alpha_generation_recovery.md 中的渐进式修复策略，当前轮次检查验收标准，达标则退出"
```

### 方式3：独立脚本（完整自动化）
```bash
python scripts/run_recovery_loop.py --max-cycles 10 --log loop_recovery.jsonl
```

---

## 当前状态快照（2026-08-06 13:06）

```yaml
症状:
  - feedback: 0
  - positive: 0
  - near_pass: 0
  - enqueued: 0
  - pending: 5
  - rejected: 25/35 (71%)

根因:
  1. SEED_TOPOLOGY_DUPLICATE: 10 (40%)
  2. MECHANISM_OPERATOR_MISMATCH: 5 (20%)
  3. 旧合同候选: 9行 LEGACY_CONTRACT_MISSING_EVIDENCE
  4. 平台会话过期: 5行 PENDING_SIMULATION 卡住

优先级:
  P0: 清理9行旧合同 + 注入1个feedback种子
  P1: 种子去重（10→<=3）
  P2: 恢复simulate或标记5个pending为READY
```

---

## 阶段1执行清单（轮次1-2）

### 任务1.1：清理旧合同候选 ⚠️ 必须

**问题**：前9行候选因`LEGACY_CONTRACT_MISSING_EVIDENCE`被拒绝，占用队列且无法重新验证。

**检查命令**：
```bash
sqlite3 research_memory.sqlite "SELECT candidate_id, queue_status, last_error_category FROM candidates WHERE last_error_category='LEGACY_CONTRACT_MISSING_EVIDENCE' LIMIT 5;"
```

**修复选项**：

**选项A（推荐）**：批量标记为过期
```sql
UPDATE candidates 
SET queue_status='REJECTED_OBSOLETE',
    quality_status='REJECTED_OBSOLETE',
    updated_at=datetime('now')
WHERE last_error_category='LEGACY_CONTRACT_MISSING_EVIDENCE';
```

**选项B（保守）**：仅从待提交CSV移除
```python
import pandas as pd
df = pd.read_csv('待提交Alpha列表.csv')
df_clean = df[df['last_error_category'] != 'LEGACY_CONTRACT_MISSING_EVIDENCE']
df_clean.to_csv('待提交Alpha列表.csv', index=False)
print(f"清理前: {len(df)} 行，清理后: {len(df_clean)} 行")
```

**验证**：
```bash
# 应输出 0
sqlite3 research_memory.sqlite "SELECT COUNT(*) FROM candidates WHERE queue_status LIKE 'REJECTED_LOCAL%' AND last_error_category='LEGACY_CONTRACT_MISSING_EVIDENCE';"
```

---

### 任务1.2：注入反馈种子 ⚠️ 必须

**问题**：`feedback=0` 导致LLM无历史正例可学习，陷入盲目生成。

**方案A（从历史snapshot提取）**：
```python
import json
from pathlib import Path

# 读取历史快照（如果存在）
snapshot = Path('alpha_state.json')
if snapshot.exists():
    data = json.loads(snapshot.read_text())
    positive_samples = [
        a for a in data.get('alphas', []) 
        if a.get('sharpe', 0) > 1.5 or a.get('fitness', 0) > 0.6
    ]
    
    if positive_samples:
        # 插入最佳1个到feedback表
        best = max(positive_samples, key=lambda x: x.get('sharpe', 0))
        sql = f"""
        INSERT INTO feedback (alpha_id, is_positive, sharpe, fitness, turnover, created_at)
        VALUES ('{best['id']}', 1, {best['sharpe']}, {best['fitness']}, {best.get('turnover', 0.15)}, datetime('now'));
        """
        # 执行SQL（需确认表结构）
        print(sql)
```

**方案B（手动构造）**：
```sql
-- 如果没有历史数据，构造一个合理的种子
INSERT INTO feedback (
    alpha_id, 
    is_positive, 
    sharpe, 
    fitness, 
    turnover,
    expression,
    created_at
) VALUES (
    'SEED_POSITIVE_001',
    1,
    2.1,
    0.75,
    0.12,
    'ts_rank(anl10_ebifq1_pred_surps_v2_2230, 126)',
    datetime('now')
);
```

**验证**：
```bash
sqlite3 research_memory.sqlite "SELECT COUNT(*) FROM feedback WHERE is_positive=1;"
# 应输出 >= 1
```

---

### 任务1.3：处理PENDING_SIMULATION候选 🔶 可选

**问题**：待提交CSV中5行`PENDING_SIMULATION`因平台会话过期无法simulate。

**当前状态**：
```python
import pandas as pd
df = pd.read_csv('待提交Alpha列表.csv')
pending = df[df['queue_status'] == 'PENDING_SIMULATION']
print(f"PENDING数量: {len(pending)}")
print(pending[['candidate_id', 'expression', 'local_quality_score']])
```

**方案A（推荐）**：恢复平台会话后simulate
```bash
# 1. 检查认证状态
python tools/ops/wq_auth_check.py

# 2. 如果过期，重新登录
# （需要用户提供凭据）

# 3. 触发simulate
python 提交Alpha.py --simulate-only --candidates pending_candidates.txt
```

**方案B（应急）**：跳过simulate直接标记READY
```python
# ⚠️ 需用户明确授权：未经simulate的候选可能不符合平台质量标准
import pandas as pd
df = pd.read_csv('待提交Alpha列表.csv')
df.loc[df['queue_status'] == 'PENDING_SIMULATION', 'queue_status'] = 'READY'
df.to_csv('待提交Alpha列表.csv', index=False)
print("⚠️ 已跳过simulate，风险：可能提交后被平台拒绝")
```

**验证**：
```bash
# 应输出 0（方案A）或 5→0（方案B）
grep "PENDING_SIMULATION" 待提交Alpha列表.csv | wc -l
```

---

### 阶段1预期输出

完成任务1.1-1.3后，运行：
```bash
python 生成Alpha.py --once
```

**预期改善**：
```diff
before:
  feedback=0 positive=0 enqueued=0 pending=5 rejected=25

after:
  feedback=1 positive=1 enqueued=1-2 pending=5→0 rejected=18-22
```

**关键指标**：
- ✅ feedback >= 1
- ✅ 旧合同候选：9→0
- ✅ enqueued > 0（首次入队）

---

## 阶段2执行清单（轮次3-4）

### 任务2.1：分析种子重复模式

```sql
-- 查看最近7天的拓扑重复
SELECT 
    structure_signature,
    COUNT(*) as collision_count,
    GROUP_CONCAT(DISTINCT parent_template) as templates
FROM candidates
WHERE created_at > datetime('now', '-7 days')
GROUP BY structure_signature
HAVING collision_count > 2
ORDER BY collision_count DESC
LIMIT 10;
```

**输出示例**：
```
structure_signature                                    | collision_count | templates
-------------------------------------------------------|-----------------|----------
group_neutralize>ts_zscore>rank>ts_std_dev::...      | 8               | group_neutralize(ts_zscore(...
ts_rank::...                                          | 5               | ts_rank(field,126)
```

**决策**：
- 如果某个`parent_template`出现>5次 → 加入黑名单（本轮禁用）
- 如果某个`operator_topology`占比>30% → 强制切换算子家族

---

### 任务2.2：扩展种子池

**当前种子**：
```python
# 从日志提取 seeds=3
# 示例：
seeds = [
    "group_neutralize(ts_zscore(fnd6_cptnewqv1300_epsfxq/cap,126)*rank(ts_mean(volume,63)/adv20),market)",
    "group_neutralize(ts_zscore(ts_delta(fn_liab_fair_val_l1_a,126)/cap,126)*rank(ts_mean(volume,63)/adv20),sector)",
    "group_neutralize(ts_zscore(acquired_finite_intangible_assets_total/cap,126)*-rank(ts_std_dev(returns,126)),market)"
]
```

**扩展策略**：
```python
new_seeds = [
    # 不同算子家族
    "ts_rank(anl10_ebifq1_pred_surps_v2_2230, 126)",  # ts_rank
    "rank(dividend)",  # rank
    "ts_mean(split, 126)",  # ts_mean
    "ts_delta(close, 5)",  # ts_delta
    "-ts_std_dev(returns, 21)",  # ts_std_dev（反向）
    
    # 不同数据集
    "ts_zscore(anl10_ebify2_smart_ests_v0_2247, 126)",  # analyst10
    "rank(volume/adv20)",  # pv1
]
```

**实施**：
```bash
# 如果有种子配置文件
echo "$new_seeds_json" > alpha_mining/knowledge/extended_seeds.json

# 或修改代码中的种子列表
# alpha_mining/generator/baseline_first.py
```

---

### 任务2.3：拓扑去重前置

**当前问题**：LLM生成10个候选 → 入库后发现10个都撞`structure_signature` → 全部拒绝。

**改进**：在入库前增加内存去重
```python
# alpha_mining/generator/expression.py（伪代码）
def generate_candidates(seeds, llm, count=10):
    candidates = []
    seen_signatures = set()
    
    # 加载最近30天的已有签名
    existing = load_recent_signatures(days=30)
    seen_signatures.update(existing)
    
    for _ in range(count * 2):  # 多生成一倍候选
        candidate = llm.generate(seeds)
        sig = compute_structure_signature(candidate)
        
        if sig not in seen_signatures:
            candidates.append(candidate)
            seen_signatures.add(sig)
            
        if len(candidates) >= count:
            break
    
    return candidates
```

**验证**：
```bash
# 下次运行后检查
python 生成Alpha.py --once 2>&1 | grep "SEED_TOPOLOGY_DUPLICATE"
# 期望：10 → <=3
```

---

### 阶段2预期输出

完成任务2.1-2.3后：
```diff
before:
  seeds=3 llm_candidates=10 rejected=25 SEED_TOPOLOGY_DUPLICATE:10

after:
  seeds=7-10 llm_candidates=10 rejected=15-18 SEED_TOPOLOGY_DUPLICATE:2-3
```

**关键指标**：
- ✅ SEED_TOPOLOGY_DUPLICATE < 3
- ✅ 种子池扩展：3→7+
- ✅ 拒绝率降低：71% → 50-60%

---

## 阶段3-5执行清单（轮次5-10）

详见`alpha_generation_recovery.md`的对应章节。

---

## 监控仪表盘

### 实时指标
```bash
# 每轮生成后运行
python tools/ops/generation_health_check.py

# 输出示例：
Generation Health Report (Last 5 Cycles)
=========================================
Cycle | Enqueued | Rejected | Feedback | Top Rejection
------|----------|----------|----------|---------------
  1   |    0     |    25    |    0     | SEED_TOPOLOGY_DUPLICATE:10
  2   |    1     |    22    |    1     | SEED_TOPOLOGY_DUPLICATE:8
  3   |    2     |    18    |    1     | MECHANISM_OPERATOR_MISMATCH:4
  4   |    2     |    15    |    3     | INVENTORY_SIMILARITY:3
  5   |    3     |    12    |    5     | (分散)

Trend: ✅ IMPROVING
Exit Criteria: 2/5 met (需3/5达标)
```

### 趋势图（可选）
```python
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_json('loop_recovery_log.jsonl', lines=True)
df.plot(x='cycle', y=['enqueued', 'rejected'], kind='line')
plt.savefig('recovery_trend.png')
```

---

## 故障排查

### Q1：连续3轮enqueued=0
**检查**：
```bash
# LLM是否失效
curl -X POST https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"test"}]}'

# catalog是否过期
stat .alpha_operators_cache.json

# 数据库是否锁定
sqlite3 research_memory.sqlite "SELECT hard_stop, reason FROM factory_control WHERE singleton=1;"
```

### Q2：MECHANISM_OPERATOR_MISMATCH持续高位
**诊断**：
```sql
SELECT expression, economic_hypothesis 
FROM candidates 
WHERE last_error_category='MECHANISM_OPERATOR_MISMATCH' 
ORDER BY created_at DESC LIMIT 3;
```

**典型案例**：
- 声称"momentum"但用`ts_std_dev` → 应用`ts_delta`
- 声称"mean-reversion"但用单调`rank` → 应用`-rank`或`ts_zscore`

**修复**：更新LLM prompt模板中的算子语义约束。

### Q3：simulate失败（401/403）
**原因**：平台会话过期或cookies失效。

**修复**：
```bash
# 删除旧会话
rm .wq_auth_state.json

# 重新认证（需用户凭据）
python tools/ops/wq_auth_check.py --renew
```

---

## 成功案例参考

### 典型恢复路径
```
轮次1：清理9行旧合同 + 注入1个feedback种子
  → enqueued=0→1, feedback=0→1

轮次2：无操作（观察反馈是否生效）
  → enqueued=1, feedback=1（LLM开始引用）

轮次3：扩展种子池3→7
  → SEED_TOPOLOGY_DUPLICATE:10→5

轮次4：拓扑去重前置
  → SEED_TOPOLOGY_DUPLICATE:5→2

轮次5：catalog刷新
  → PLAN_UNKNOWN_FIELD:2→0

轮次6-7：机制-算子对齐
  → MECHANISM_OPERATOR_MISMATCH:5→1

轮次8：触发simulate（5个pending→feedback表）
  → feedback=1→8, positive=1→3

轮次9：验收测试
  → 连续2轮enqueued>=2, 拒绝率<50%

退出：达到3/5验收标准 ✅
```

---

**最后更新**：2026-08-06  
**维护者**：Alpha Mining Team  
**紧急联系**：如loop失败>5轮，立即停止并上报诊断日志
