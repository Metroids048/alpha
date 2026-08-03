# WorldQuant Alpha 生成质量链路冻结版解决方案

**文件名：** `02-frozen-solution-v1.0.md`  
**Frozen Solution Version：** `v1.0`  
**方案性质：** 只读设计与施工冻结说明，本阶段禁止修改代码  
**诊断基线：** 用户与两次独立审查共同确认的 `main @ 7a7f035eb461b14ddc404f84f6052d4d24b1008b`；实施开始时必须重新记录实际 HEAD，不得默认仍相同  
**唯一业务目标：** 将当前“候选数量优先、生成/模拟/反馈分裂、质量判断失真”的流程，收口为一个少量优选、真实模拟、即时反馈、定向修复、全部硬门槛通过后才输出的单一生成闭环。

---

## 0. 输入有效性检查与冻结结论

### 0.1 诊断报告有效性

| 检查项 | 结论 | 依据 |
|---|---|---|
| 每个核心问题有明确证据 | 通过 | 已有代码路径、真实 CSV 数据和独立复算结果 |
| 每个核心问题有明确根因 | 通过 | 已定位到具体函数、调用关系和编辑事故 |
| 不同问题已去重 | 通过，但需按本方案重新归并 | HTTP 400、重复 archive、recovery probe 冲突均属于更上层根因的结果，不单独扩成新架构任务 |
| 目标与验收标准清晰 | 通过 | Sharpe、Fitness、Turnover、相关性、单入口和单 CSV 均有明确要求 |
| 禁止修改范围清晰 | 通过 | 不重写提交 Guard、认证恢复、429 永久循环和无关模块 |
| 是否存在证据不足问题 | 无阻塞项 | 真实平台能否最终找到合格 Alpha 仍是外部结果，不是实施承诺，也不阻塞代码方案冻结 |

### 0.2 已独立确认的事实

1. 20 个 `archive_*.csv`，共 5,012 行，但只有 536 个唯一表达式、84 个字段骨架、27 个候选 ID。
2. `alpha_mining/generation/screening.py:50-90` 定义了 `UNKNOWN_FIELD`、`UNKNOWN_OPERATOR`，但生产 screening 没有执行字段和算子 catalog 校验。
3. `alpha_mining/generator/baseline_first.py:19-27` 的 `classify_baseline()` 只检查 Sharpe。
4. `alpha_mining/factory/orchestrator.py:294-300` 在没有动态 Gate 时默认 Sharpe 阈值为 1.25，而不是用户要求的 1.57。
5. `alpha_mining/generator/llm_consultant_bridge.py:74` 调用 `self.llm.invoke()`，但仓库的结构化 LLM 协议只有 `generate_json()`。
6. `CandidateFeedbackStore.record()` 在当前生产 factory/generation 路径没有调用点。
7. `alpha_mining/scheduler/arm_metrics.py:126` 调用不存在的 `self.stats(arm)`；第 173-196 行是缺失方法签名后遗留的孤儿方法体。
8. `FactoryOrchestrator._record_arm()` 每个结果调用一次 `record_window()`，输入列表长度为 1，无法形成设计要求的 20 条 evidence window；异常又被 `try/except` 降级为 warning。
9. 135 条 simulate 结果中只有 27 条 COMPLETE；89 条 HTTP 400；有效结果中没有一条同时满足 Sharpe、Fitness、Turnover 门槛。
10. 当前实际使用的旧入口把生成、批量 simulate、反馈分析和人工 Prompt 分散在多个脚本中；新 Factory 架构和旧实际运行链路互不统一。

### 0.3 有限纠正与问题清洗

以下现象不再单独建立方案，避免重复修复：

- **89 次 HTTP 400**：主要归入 P1-002“simulate 前 catalog 校验缺失”；Gateway 丢失响应详情只是诊断能力缺失，作为同一方案的辅助修改。
- **13 次 recovery probe 冲突**：主要由分裂式批量 simulate 调度触发，归入 P1-001“唯一串行质量工作流”；不单独重写平台访问状态机。
- **`exact_copy_1~5`、archive 重复和清空哈希**：均归入 P1-001“数量填充及旧 CSV 拼接链路”；不另建 ingest 子系统。
- **World quant 文件只打印数量、LLM 接口失效**：共同导致生成智能没有真实进入候选决策，合并为 P1-004。
- **ResearchArmTracker、FeedbackStore、CandidateGenerationService 的反馈失效**：共同属于 P1-005，不拆成三个相互重叠的问题。

### 0.4 诊断冲突的最终裁决

以下两个结论同时成立，不互相否定：

1. Factory 架构比旧脚本更适合作为长期权威实现，但当前至少有 LLM、反馈、质量判定等关键代码错误，尚未真正跑通。
2. 用户两天实际运行的是 `生成高质量Alpha.py → v50 → 候选 CSV → 批量 simulate → 人工 Prompt` 的旧分裂链路，并未使用完整 Factory 闭环。

因此，本方案不再增加第三套旁路，也不把全部逻辑重新塞进一个巨型文件。最终选择为：

> **以 `alpha_mining` 的 Factory、请求幂等、PlatformGateway 和 SQLite 状态能力作为唯一内部机制；创建唯一用户入口 `生成Alpha.py`；有选择地迁移 v50 中仍有效的候选、设置和修复经验；旧脚本退出生产调用。**

### 0.5 最终结论

**方案已冻结，可以进入实施阶段。**

---

# 1. 冻结问题基线

## P1-001：实际生成链路以数量填充和 CSV 拼接为中心，不是质量闭环

### 现象

- `生成高质量Alpha.py` 默认 `batch-size=150`、`max-payloads=300`、无限循环。
- 主动放宽相似度和历史骨架限制。
- 连续 5 轮无新增后归档 CSV、清空 `existing_hashes`，重新接受历史表达式。
- 生成、simulate、分析、人工 LLM 改进分布在多个脚本和 CSV 中。
- simulate 结果不会在同一进程立即改变下一轮候选预算。

### 根因

旧流程拆分时，把“提交权限”与“生成质量闭环”一起拆断；候选数量成为循环停止条件，CSV 变成隐式状态总线，SQLite 中的候选、请求和反馈能力没有成为实际运行主线。

---

## P1-002：字段、算子和字段—数据集关系没有在 simulate 前执行生产级校验

### 现象

- 89 条 HTTP 400。
- 出现 `book_value`、`fcf`、`ebitda`、`total_assets` 等未确认真实字段，以及 `ts_stddev` 等错误算子名称。
- `RejectionReason.UNKNOWN_FIELD/UNKNOWN_OPERATOR` 仅定义、没有生产触发路径。
- Gateway 非 2xx 时只报告状态码，丢失经过脱敏后可用于诊断的响应详情。

### 根因

生产 `CandidateScreeningPolicy` 只做 AST、group gate、exact hash 和 skeleton 校验，没有接入已经存在的同步 catalog 元数据与 `LocalExpressionValidator`；错误表达式直到平台 POST 才暴露。

---

## P1-003：质量分类与最终输出门槛不等同于 WorldQuant 提交门槛

### 现象

- `classify_baseline()` 只检查 Sharpe。
- 默认 Sharpe 阈值可能为 1.25。
- Fitness、Turnover、自相关、生产相关性和 mandatory checks 没有共同决定 PASS。
- 旧脚本可能先把未模拟候选写入“高质量”CSV。

### 根因

“基线是否值得继续研究”和“是否可以进入待提交清单”被错误复用为同一个三态分类；没有唯一的最终质量决策对象和唯一 READY 输出规则。

---

## P1-004：LLM 与 World quant 知识没有真实参与候选生成

### 现象

- `LLMConsultantBridge` 调用不存在的 `invoke()`，失败后回退固定模板。
- 回退不会使本轮进入明确质量降级状态。
- `World quant/` 文件被读取或打印，但内容没有进入结构化生成请求、候选元数据和修复决策。
- 索引引用的 `alpha_inspiration/posts/` 正文可能不存在，却有脚本宣称加载了 151 篇。

### 根因

LLM 协议未统一；知识目录没有建立可检索、可追踪的仓库接口；旧自动化脚本只通过 subprocess 启动另一个完全不知道知识内容的进程。

---

## P1-005：模拟反馈从代码层面未能改变下一轮生成预算

### 现象

- `ResearchArmTracker.stats()` 方法签名丢失。
- 调用方错误地对单条结果调用 `record_window()`。
- 异常被捕获后仅打印 warning。
- `CandidateFeedbackStore` 存在但没有接入成功、失败和 UNKNOWN 路径。
- `CandidateGenerationService._feedback`、`_idea_generator` 未参与生成决策。
- family 权重最多只改变排序，不限制配额或停止低收益方向。

### 根因

一次编辑事故破坏 ArmTracker；随后缺少生产级端到端测试，使错误被 warning 掩盖。反馈存储、arm 统计和候选预算分别存在，但没有形成一个事务后可观察的闭环。

---

# 2. 目标架构与不可变业务规则

## 2.1 唯一用户流程

```text
python 生成Alpha.py
  → 加载真实 catalog 与 World quant 知识
  → 选择一个研究方向
  → 生成最多 3 个初始候选
  → 本地字段/算子/数据集/语法硬校验
  → 串行真实 simulate
  → 全指标质量决策
  → 只对 NEAR_PASS 做有限定向修复
  → 反馈写入 SQLite 并立即改变下一轮预算
  → 全部门槛通过才原子 upsert 到 待提交Alpha列表.csv

python 提交Alpha.py
  → 只读取 READY_TO_SUBMIT 记录及已有 alpha_id
  → 继续使用现有 Ledger、Description、SubmissionGuard 和人工确认机制
  → 不再次承担候选生成或批量 simulate
```

## 2.2 唯一事实来源

| 业务信息 | 唯一事实来源 |
|---|---|
| 候选身份、请求状态、恢复状态 | `research_memory.sqlite` 中的 `simulation_requests` 及 identity 表 |
| 模拟 terminal outcome | `candidate_outcomes` |
| family/arm 统计和预算状态 | `research_arm_metrics`、`research_arm_observation_windows`，由 terminal feedback 驱动 |
| 平台 catalog | 现有同步生成的本地平台 catalog 缓存；不得使用自然语言猜测字段 |
| 可提交结果 | SQLite 中 READY_TO_SUBMIT 的投影；`待提交Alpha列表.csv` 只是原子导出视图 |
| 历史 archive、simulate_results、旧候选 CSV | 只读诊断/测试 fixture，不再作为运行状态来源 |

## 2.3 质量门槛

最终 READY_TO_SUBMIT 使用 AND 关系：

```text
Sharpe >= max(1.57, 当前平台 LOW_SHARPE 最低门槛)
Fitness > max(1.00, 当前平台 LOW_FITNESS 最低门槛)
Turnover >= max(0.01, 当前平台 LOW_TURNOVER 最低门槛)
Turnover <= min(0.70, 当前平台 HIGH_TURNOVER 最高门槛)
Simulation status == COMPLETE
所有 mandatory checks 不得为 FAIL/FAILED/REJECTED
SELF_CORRELATION == PASS
PROD_CORRELATION/PRODUCTION_CORRELATION == PASS，或符合现有 Submit Guard 明确允许的状态
结果具有可确认 alpha_id
```

规则：

- 动态平台门槛可以比用户门槛更严格，不能更宽松。
- 缺失相关性或 mandatory check 不得当作 PASS，状态为 `WAITING_CHECKS`。
- 不允许用综合分数抵消某一个硬门槛失败。
- 不承诺代码修改后市场一定产生合格 Alpha；承诺不合格结果绝不进入待提交 CSV。

## 2.4 冻结预算

```text
每周期研究方向：1
初始 hypothesis：最多 3
初始候选：最多 3
进入修复的父候选：最多 2
每个父候选最大修复次数：4
单周期真实 simulate 最大次数：12
默认 24 小时 simulate 预算：24
单周期 READY 输出上限：1
simulate 并发：1
无合格候选时允许输出 0 条
```

不得为了填满任何数量目标降低筛选、清空历史或重复旧 skeleton。

---

# 3. 冻结方案

# S-001：建立唯一的少量优选生成入口与串行质量工作流

## 1. 对应问题

- **问题编号：** P1-001
- **问题现象：** 候选数量爆炸；生成、simulate、反馈和人工改进分散；CSV 被当作流程状态。
- **已确认根因：** 实际运行仍是旧 v50 脚本拼接链路，Factory 状态能力没有成为唯一主线；循环以“填满候选数”为目标。

## 2. 目标状态

1. 用户只运行 `python 生成Alpha.py` 完成生成、simulate、判定、修复和输出。
2. 同一 Python 进程内完成完整质量闭环，禁止通过 subprocess 串接旧脚本。
3. 每周期最多 3 个初始候选、12 次 simulate；可以产生 0 个结果。
4. 唯一用户结果文件为 `待提交Alpha列表.csv`。
5. 旧脚本不再被文档、PowerShell、定时任务或生产入口调用。

## 3. 根因处理方式

当前错误机制是：v50 大量生成 → 候选 CSV → 独立批量 simulate → 结果 CSV → 人工 Prompt。方案在最上层工作流边界切断该机制：由一个 `QualityAlphaWorkflow` 在内存和 SQLite 中编排完整循环，CSV 只在 READY 后导出。

不选择仅把 `batch-size=150` 改成 20，因为数量降低后，字段校验、质量判定和反馈断路仍然存在；也不新增 `alpha_generate.py run/prompt/ingest` 三段式流程，因为那仍保留人工和文件拼接。

## 4. 修改范围

### 创建

- `生成Alpha.py`
- `alpha_mining/factory/quality_workflow.py`
- `alpha_mining/storage/ready_alpha_csv.py`
- `tests/test_quality_generation_workflow.py`
- `tests/test_single_generation_entrypoint.py`

### 修改

- `alpha_mining/factory/orchestrator.py`
- `alpha_mining/factory/runtime.py`（仅使现有 runtime 委托同一 workflow；如当前版本不存在则不创建替代副本）
- `alpha_mining/generation/service.py`
- `alpha_mining/config.yaml`
- `提交Alpha.py`（仅修改输入职责，不修改 Guard、Ledger、Description 和真实提交保护）
- 直接引用旧入口的 PowerShell/权威使用说明

### 实施后退出生产调用

- `生成高质量Alpha.py`
- `批量simulate验证.py`
- `生成Alpha_完全自动化.py`
- `自动迭代闭环.py`
- `迭代提交Alpha.py`
- `启动Alpha主线.py` 中与 Alpha 生成重复的入口职责

`auto_alpha_pipeline_rebuilt_v50.py` 本轮不重写、不删除；仅允许作为迁移参考和被精确适配的内部能力来源，禁止继续作为用户生产入口。

## 5. 文件级修改说明

### 文件：`生成Alpha.py`

- **当前职责：** 不存在。
- **需要修改：** 创建唯一 CLI；解析数据库、输出路径、最大周期、是否连续运行、可选真实 simulate 授权；构造 `QualityAlphaWorkflow`。
- **输入：** `research_memory.sqlite`、catalog 缓存、`World quant/`、环境认证状态、冻结预算。
- **输出：** 控制台摘要、SQLite 状态、`待提交Alpha列表.csv`。
- **调用方：** 用户、允许的 PowerShell/调度器。
- **被调用方：** `QualityAlphaWorkflow`。
- **不允许：** import v50 后自行生成大量候选；subprocess 调用旧脚本；自动 submit。

### 文件：`alpha_mining/factory/quality_workflow.py`

- **当前职责：** 不存在。
- **需要修改：** 创建 `QualityGenerationConfig`、`QualityCycleSummary`、`QualityAlphaWorkflow.run_cycle()`。
- **修改后职责：** 唯一质量编排器；负责预算、候选筛选顺序、模拟、质量判定、修复循环、反馈和 READY 导出。
- **输入：** Candidate service、Orchestrator、Quality evaluator、Feedback store、Knowledge repository、CSV store。
- **输出：** 周期摘要和可审计状态。
- **不允许：** 自己实现 HTTP、SQL schema、catalog parser、LLM provider 或提交逻辑。

### 文件：`alpha_mining/factory/orchestrator.py`

- **当前职责：** 候选生成与模拟编排混合，使用 Sharpe-only 结果分类。
- **需要修改：** 提取公开的单候选执行契约，例如 `execute_candidate(proposal, settings) -> CandidateExecutionResult`；现有 `run_simulate()` 必须复用该方法，不保留另一份模拟完成逻辑。
- **输出契约：** `request_hash`、`alpha_id`、外部状态、metrics、checks、sanitized error、lease/finalize 状态。
- **不允许：** 在 Orchestrator 内重新实现 quality gate 或 family budget。

### 文件：`alpha_mining/generation/service.py`

- **当前职责：** 尝试循环填满 `limit`，feedback 基本未使用。
- **需要修改：** 接受冻结的 `GenerationBudget` 与 feedback snapshot；最多返回 3 个初始候选；允许返回空；移除“为满足 limit 持续尝试”的数量填充语义。
- **不允许：** 清空 exact hash；读写候选 CSV；直接调用平台。

### 文件：`alpha_mining/storage/ready_alpha_csv.py`

- **当前职责：** 不存在。
- **需要修改：** 从 READY 记录原子 upsert `待提交Alpha列表.csv`；使用临时文件后 replace；按 `alpha_id + exact_hash` 幂等。
- **不允许：** 写入 NEAR_PASS、FAR_FAIL、FAILED、UNKNOWN、WAITING_CHECKS。

### 文件：`提交Alpha.py`

- **当前职责：** 读取 CSV 后再次批量 simulate，并继续提交链路。
- **需要修改：** 要求输入行已有 `alpha_id`、`quality_status=READY_TO_SUBMIT`、完整质量证据；停止重复 simulate；继续调用现有 ledger sync、description、dry-run、SubmissionGuard 和 execute confirmation。
- **不允许改动：** 真实提交默认关闭、确认短语、Guard、相关性检查、幂等 delivery。

## 6. 数据和状态变化

### 修改前

```text
候选 CSV 创建状态 → simulate_results.csv 更新结果 → 人工 Prompt → 手工粘贴回 CSV
```

### 修改后

```text
CandidateProposal
→ simulation_requests claim/lease
→ PlatformGateway simulate
→ candidate_outcomes terminal record
→ quality decision
→ arm/family feedback
→ repair lineage（如允许）
→ READY SQLite 状态
→ 原子投影到待提交Alpha列表.csv
```

- 状态创建：CandidateGenerationService。
- 请求更新：SimulationRequestStore。
- terminal outcome 更新：CandidateFeedbackStore。
- READY 导出：ReadyAlphaCsvStore。
- 幂等键：`request_hash`、`exact_hash`、`alpha_id`。
- 事务：外部结果 finalize 与本地成功写入沿用现有请求事务；CSV 导出在事务后进行，失败只留下 warning 和可重试导出状态，不能覆盖真实模拟结果。

## 7. 接口契约

```python
@dataclass(frozen=True)
class QualityGenerationConfig:
    max_initial_candidates: int = 3
    max_repair_parents: int = 2
    max_repairs_per_parent: int = 4
    max_simulations_per_cycle: int = 12
    max_simulations_per_24h: int = 24
    max_ready_per_cycle: int = 1
    simulation_concurrency: int = 1
```

```python
class QualityAlphaWorkflow:
    def run_cycle(self, config: QualityGenerationConfig) -> QualityCycleSummary: ...
```

`QualityCycleSummary` 至少包含 generated、local_rejected、simulated、near_pass、far_fail、waiting_checks、ready、failed、unknown、budget_exhausted 和 deferred_reason。

## 8. 旧逻辑处理

- 旧生成、批量 simulate、人工 Prompt 脚本：**迁移后删除**。
- v50：**保留但停止生产调用**；只有通过适配器迁移且有测试的能力可被新流程复用。
- 历史 CSV：提取脱敏 fixture 后从仓库删除或移到不被运行代码扫描的历史资料目录；不得继续作为队列。

## 9. 实施步骤

1. 写入口和预算的失败测试，确认旧入口数量和 subprocess 设计被捕获。
2. 创建 `QualityAlphaWorkflow` 空骨架和明确依赖，不接平台。
3. 提取 `FactoryOrchestrator.execute_candidate()`，运行现有 factory 测试。
4. 接入冻结预算，证明候选不足时返回 0 而不是放宽筛选。
5. 接入 S-002、S-003、S-004、S-005 后再允许 workflow 完整运行。
6. 修改 `提交Alpha.py` 只消费 READY 结果。
7. 完整验收通过后，才删除旧入口；删除后重新运行 call graph 和全量测试。

## 10. 测试方案

### T-101：修改前复现测试

- **前置：** 当前旧脚本。
- **操作：** 静态解析默认参数和 archive reset 逻辑。
- **预期：** 证明默认目标 150、无限循环和 `existing_hashes.clear()` 存在。
- **对应：** P1-001。

### T-102：根因级测试

- 调用新 workflow，生成器返回 100 个候选。
- 预期只取最多 3 个初始候选，simulate 总数不超过 12，不创建 archive，不清空 hash。

### T-103：模块集成测试

- Fake Gateway 返回 FAR_FAIL、NEAR_PASS、READY 三种结果。
- 验证同一进程完成 simulate、反馈、修复和 CSV 投影。

### T-104：端到端测试

- 从 `生成Alpha.py` 进入临时数据库和 Fake Gateway。
- 验证不调用 subprocess；仅 READY 行进入唯一 CSV。

### T-105：回归测试

- 原 factory 请求恢复、UNKNOWN、429 状态、description 和 submit guard 相关测试全部保持通过。

## 11. 验收标准

- **A-101：** 仓库权威说明只列 `生成Alpha.py` 为生成入口。
- **A-102：** 运行一周期初始候选 ≤3、simulate ≤12、并发=1。
- **A-103：** 0 个合格候选时程序正常完成，CSV 无新增。
- **A-104：** 旧脚本不在任何生产 import、PowerShell、CI、文档调用中。
- **A-105：** `提交Alpha.py` 不再次调用 `PlatformGateway.simulate()`。

## 12. 风险和回滚

- **风险：** 从旧入口切换后遗漏某个 v50 有效设置策略。
- **发现：** shadow fixture 和新旧候选设置对比测试。
- **回滚：** 回滚入口切换 Commit；不得恢复 CSV 手工拼接作为长期双轨。
- **数据：** 实施前备份 SQLite；CSV 为投影，可从 READY 状态重建。

## 13. 明确禁止事项

- 不得把 150 改成另一个大数字后宣称完成。
- 不得新增 run/prompt/ingest 三段式人工流程。
- 不得在 workflow 里复制 Gateway、Guard 或 SQL schema。
- 不得提前删除 v50 和旧脚本，必须先完成全部验收。

---

# S-002：把真实 catalog 校验接入生产 screening，并保留安全错误证据

## 1. 对应问题

- **问题编号：** P1-002
- **问题现象：** UNKNOWN 枚举不可达；大量 400；字段/算子直到平台才发现错误。
- **根因：** `CandidateScreeningPolicy` 没有调用现有 catalog validator。

## 2. 目标状态

所有候选在 claim 和 simulate 前必须通过：

1. AST/FASTEXPR 语法；
2. operator 存在；
3. operator arity；
4. field 存在；
5. field 属于候选声明 dataset；
6. catalog 与 region/universe/delay 未过期；
7. group gate、exact hash 和 skeleton gate。

未知字段或算子必须 `LOCAL_REJECTED`，Gateway 调用次数保持 0。

## 3. 根因处理方式

复用现有 `LocalExpressionValidator` 和平台同步缓存，不创建第三套字段白名单。将 validator 作为 `CandidateScreeningPolicy` 的强制依赖，从候选筛选边界切断无效表达式进入平台的路径。

Gateway 响应详情仅用于诊断未被本地捕获的平台错误，不代替本地验证。

## 4. 修改范围

- `alpha_mining/generation/validation.py`
- `alpha_mining/generation/screening.py`
- `alpha_mining/generation/service.py`
- `alpha_mining/platform/catalog.py`
- `alpha_mining/platform/gateway.py`
- `tests/test_catalog_screening_production.py`
- 扩展 `tests/test_offline_candidate_generation.py`

## 5. 文件级修改说明

### `alpha_mining/generation/validation.py`

- 将 `LocalExpressionValidator` 的输入从具体 `MetadataCache` 放宽为明确的只读 Catalog 协议。
- 新增 `expected_dataset_id`、region、universe、delay 上下文校验。
- BASE_VARS 和 GROUPS 使用现有注册表，不误判为字段。

### `alpha_mining/generation/screening.py`

- `CandidateScreeningPolicy` 构造时必须获得 validator。
- `screen_expression()` 在 canonical dedup 前调用 validator。
- 将 ValidationIssue 映射到 `INVALID_SYNTAX`、`UNKNOWN_OPERATOR`、`UNKNOWN_FIELD`、`INVALID_ARITY`、`FIELD_DATASET_MISMATCH`。
- 新增拒绝原因只能针对已确认的 validator 结果，不得用通用 `INVALID` 掩盖。

### `alpha_mining/platform/catalog.py`

- 保持现有同步行为；提供一个只读 adapter 将 `.alpha_*_cache.json` 暴露为 validator 所需结构。
- 不改变平台分页、认证和网络边界。

### `alpha_mining/platform/gateway.py`

- 非 2xx simulation submit/poll 错误加入最多 500 字符的脱敏 response body。
- 不记录 headers、Cookie、Authorization、Token。
- 错误结构例如：`simulation submit failed with HTTP 400: <sanitized detail>`。

## 6. 数据和状态变化

- catalog 仍使用当前同步缓存，不新增第二套缓存文件。
- 本地拒绝写入 factory event/candidate outcome，状态 `LOCAL_REJECTED`，不创建外部 request lease。
- catalog 缺失或过期时，整个周期 `CATALOG_UNAVAILABLE`，不得回退自然语言字段。

## 7. 接口契约

```python
class ExpressionCatalog(Protocol):
    operators: Mapping[str, OperatorMetadata]
    fields: Mapping[str, FieldMetadata]
    region: str
    universe: str
    delay: int
    fetched_at: datetime
```

```python
validator.validate(expression, *, expected_dataset_id) -> tuple[ValidationIssue, ...]
```

## 8. 旧逻辑处理

- `UNKNOWN_FIELD/UNKNOWN_OPERATOR` 空枚举机制：**修改为真实路径**。
- offline validator：**保留并改为共享协议**。
- 旧脚本中的硬编码字段检查：**停止调用，不迁移重复实现**。

## 9. 实施步骤

1. 用历史 400 表达式建立脱敏 fixture。
2. 写未知字段、错误算子、错误 arity、字段—dataset 不匹配失败测试。
3. 使 production screening 调 validator。
4. 验证本地拒绝时 Fake Gateway 调用为 0。
5. 最后修改 Gateway 错误详情和脱敏测试。

## 10. 测试方案

- **T-201 修改前复现：** `ts_stddev(close,20)`、`rank(not_in_catalog)` 当前 production screening 未拒绝。
- **T-202 根因级：** 上述表达式分别返回 `UNKNOWN_OPERATOR`、`UNKNOWN_FIELD`，simulate 未调用。
- **T-203 模块集成：** 合法字段但错误 dataset 返回 `FIELD_DATASET_MISMATCH`。
- **T-204 端到端：** `poor_quality_run.json` 中可归类的 400 样例全部在本地停止。
- **T-205 回归：** 合法 expression 继续通过；Gateway 错误文本脱敏且不泄露测试 secret。

## 11. 验收标准

- **A-201：** `UNKNOWN_FIELD` 和 `UNKNOWN_OPERATOR` 有生产测试命中。
- **A-202：** 本地无效表达式对应平台调用次数为 0。
- **A-203：** catalog 缺失/过期时 fail-closed。
- **A-204：** 下一轮授权真实运行中 HTTP 400 数量为 0；若平台仍返回 400，日志包含脱敏详情并可归类。

## 12. 风险和回滚

- 风险：缓存不完整导致所有候选被拒绝。
- 发现：`CATALOG_UNAVAILABLE` 明确计数和同步指令。
- 回滚：回滚 validator 接入 Commit，但不得回到“自然语言猜字段”；先修 catalog 同步。

## 13. 明确禁止事项

- 不得把未知字段自动替换成名字相似字段后直接 simulate。
- 不得硬编码用户示例字段为白名单。
- 不得在错误中输出响应 header 或认证信息。

---

# S-003：建立唯一全指标质量决策和 READY 输出规则

## 1. 对应问题

- **问题编号：** P1-003
- **根因：** Baseline 分类与最终可提交判定混用，最终 PASS 只看 Sharpe。

## 2. 目标状态

系统对每次 terminal simulation 产生一个且仅一个 `QualityDecision`：

```text
READY_TO_SUBMIT
NEAR_PASS
FAR_FAIL
LOCAL_REJECTED
FAILED
UNKNOWN
WAITING_CHECKS
```

只有 `READY_TO_SUBMIT` 可以进入 `待提交Alpha列表.csv`。

## 3. 根因处理方式

创建独立的最终质量决策模块，并使 Factory、Feedback、CSV 和 Submit 输入共同使用。`classify_baseline()` 仅保留为 legacy/早期研究参考，不再决定最终队列状态。

## 4. 修改范围

### 创建

- `alpha_mining/quality/__init__.py`
- `alpha_mining/quality/decision.py`
- `tests/test_quality_decision.py`

### 修改

- `alpha_mining/factory/orchestrator.py`
- `alpha_mining/factory/quality_workflow.py`
- `alpha_mining/generator/baseline_first.py`
- `alpha_mining/generation/feedback.py`
- `alpha_mining/storage/ready_alpha_csv.py`
- `提交Alpha.py`
- `alpha_mining/storage/migrations.py`

## 5. 文件级修改说明

### `alpha_mining/quality/decision.py`

定义：

```python
@dataclass(frozen=True)
class QualityThresholds: ...

@dataclass(frozen=True)
class QualityDecision:
    status: QualityStatus
    reasons: tuple[str, ...]
    repairable: bool
    thresholds_used: Mapping[str, float]
```

`evaluate_quality()` 必须执行全部 AND 门槛和 mandatory checks。

### `baseline_first.py`

- 保留 `BaselineOutcome` 用于“值得继续研究”的兼容场景。
- 文档明确它不是最终可提交判定。
- Factory 不再把 `BaselineOutcome.PASS` 写成最终 PASS。

### `storage/migrations.py`

新增迁移版本 18，统一由 migration 创建/扩展 `candidate_outcomes`，至少增加：

```text
quality_status
quality_reasons_json
self_correlation
prod_correlation
knowledge_refs_json
parent_candidate_id
repair_action
operator_topology
region
universe_name
delay
```

`CandidateFeedbackStore` 不再自行拥有与 migration 冲突的 schema 定义；初始化时调用迁移并验证列存在。

## 6. 数据和状态变化

- 每个 `request_hash` 只有一条 terminal outcome，first terminal write wins。
- UNKNOWN 不得覆盖为 FAILED，也不得自动立即重放。
- WAITING_CHECKS 可以在只读平台检查刷新后升级为 READY/NEAR/FAR，但必须记录状态转换事件。
- READY CSV 从 SQLite 重新投影，CSV 不是可修改状态源。

## 7. 接口契约

```python
evaluate_quality(
    *, metrics: Mapping[str, float | None],
    checks: Sequence[Mapping[str, object]],
    simulation_status: str,
    alpha_id: str,
    live_gates: GateSnapshot,
    user_floor: QualityThresholds,
) -> QualityDecision
```

NEAR_PASS 冻结规则：

- Sharpe ≥ 1.25；
- Fitness ≥ 0.75；
- 无 catalog/语法/数据覆盖硬失败；
- 只允许一个主要可修复维度；
- 严重高换手且低 Sharpe/Fitness 必须 FAR_FAIL。

## 8. 旧逻辑处理

- Sharpe-only final PASS：**替换**。
- v50 内部静态分数：**允许作为候选排序参考，不得作为 READY 依据**。
- `SubmissionJudge`：**保留**，只对 READY 候选排序，不代替硬门槛。

## 9. 实施步骤

1. 写纯函数表驱动测试。
2. 建立迁移和旧数据库升级测试。
3. Factory 切换为 `QualityDecision`。
4. Feedback 持久化完整 decision。
5. Ready CSV 只查询 READY。
6. Submit 输入增加 fail-closed 校验。

## 10. 测试方案

- **T-301 修改前复现：** Sharpe 1.60、Fitness 0.20、Turnover 1.73 被旧逻辑判 PASS。
- **T-302 根因级：** 同样输入必须 FAR_FAIL/HIGH_TURNOVER+LOW_FITNESS。
- **T-303 模块集成：** 缺 SelfCorr 时 WAITING_CHECKS；SelfCorr FAIL 时 FAR_FAIL。
- **T-304 端到端：** 只有全部硬门槛通过的行进入 CSV。
- **T-305 回归：** Submit Guard 仍会再次 fail-closed，不因 CSV READY 绕过。

## 11. 验收标准

- **A-301：** Sharpe 0.74/Fitness 0.18/Turnover 1.73 不进入 CSV。
- **A-302：** 所有 READY 行同时满足硬门槛和 checks。
- **A-303：** 缺失相关性证据的行不进入 CSV。
- **A-304：** 默认有效 Sharpe 下限不低于 1.57。

## 12. 风险和回滚

- 风险：平台 check 名称变化导致 WAITING_CHECKS 增多。
- 发现：未识别 check 记录为明确 reason。
- 回滚：回滚 check parser 映射，不得把缺失值当 PASS。

## 13. 明确禁止事项

- 不得用平均分或加权总分替代硬门槛。
- 不得为使 CSV 非空降低阈值。
- 不得把平台动态 Gate 当作可以低于用户门槛的覆盖值。

---

# S-004：修复结构化 LLM 契约，并将 World quant 知识变成每轮可追踪输入

## 1. 对应问题

- **问题编号：** P1-004
- **根因：** LLM 协议不一致；知识目录没有仓库接口；旧脚本只打印知识数量。

## 2. 目标状态

1. `LLMConsultantBridge` 使用 `generate_json(system_prompt, user_prompt, json_schema)`。
2. 每轮生成必须拥有非空 `KnowledgeContext`；候选记录明确 `knowledge_refs` 和经济逻辑。
3. LLM 最多返回 3 个候选；失败时 fallback 最多 1 个，并明确标记质量降级。
4. 不存在的 inspiration 正文必须报告 `KNOWLEDGE_INCOMPLETE`，不能宣称加载 151 篇。

## 3. 根因处理方式

在生成边界统一 LLM Protocol，同时增加只读 `WorldQuantKnowledgeRepository`。知识不是全量拼入 Prompt，而是按当前 dataset、字段描述、经济机制和上一轮失败类别选择少量相关片段；候选必须携带引用，才能进入 screening。

## 4. 修改范围

### 创建

- `alpha_mining/knowledge/worldquant_repository.py`
- `tests/test_worldquant_knowledge_integration.py`
- `tests/test_llm_consultant_contract.py`

### 修改

- `alpha_mining/generator/llm_consultant_bridge.py`
- `alpha_mining/llm/deepseek.py`（仅在协议适配确有需要时；不改变网络安全和 secret 处理）
- `alpha_mining/generation/service.py`
- `alpha_mining/generator/consultant_generator.py`
- `alpha_mining/factory/quality_workflow.py`

## 5. 文件级修改说明

### `worldquant_repository.py`

定义：

```python
@dataclass(frozen=True)
class KnowledgeSnippet:
    ref_id: str
    path: str
    heading: str
    content_hash: str
    text: str
    tags: tuple[str, ...]

@dataclass(frozen=True)
class KnowledgeContext:
    snippets: tuple[KnowledgeSnippet, ...]
    completeness_status: str
    missing_refs: tuple[str, ...]
```

- 扫描实际存在的 `World quant/**/*.md`。
- 按标题/段落切片和内容哈希去重。
- 根据当前字段描述、strategy family、failure category 做确定性关键词评分。
- 每轮最多 5 个片段，总文本上限 6,000 字符。
- 目录缺失或无可读内容时 `KNOWLEDGE_UNAVAILABLE`，不得假装已使用。

### `llm_consultant_bridge.py`

- 删除 `invoke()` 调用。
- 使用结构化 JSON schema。
- schema 每条候选必须有 expression、strategy_family、economic_rationale、knowledge_refs、expected_turnover_behavior、novelty_reason。
- 输出数量最大 3。
- LLM 异常写 `LLM_GENERATION_UNAVAILABLE`，不静默输出 8-14 个模板。

### `consultant_generator.py`

- 保留为确定性 fallback。
- fallback 单次最多 1 个候选。
- 只使用已经验证 catalog 字段和允许 operator。
- 标记 `generator_source=deterministic_fallback`。

### `generation/service.py`

扩展 `CandidateProposal`：

```text
knowledge_refs
economic_rationale
expected_signal
expected_turnover_behavior
repair_origin
```

缺少 knowledge refs 的 LLM 候选不得进入 simulate；fallback 必须引用至少一个已加载知识规则或现有 ontology 规则。

## 6. 数据和状态变化

- 知识文件不复制进数据库全文，只持久化 ref ID、path、content hash 和候选引用。
- Prompt 日志不得保存 API key 或完整平台敏感响应。
- knowledge hash 变化可以解除 DEAD direction 的冻结；普通重启不能。

## 7. 接口契约

```python
repository.retrieve(
    *, dataset: str,
    fields: tuple[FieldMetadata, ...],
    mechanism: str,
    failure_category: str | None,
    limit: int = 5,
) -> KnowledgeContext
```

结构化 LLM schema 必须由测试固定，禁止自由文本逐行解析。

## 8. 旧逻辑处理

- `生成Alpha_完全自动化.py` 中“读取后只打印”的知识逻辑：**删除**。
- `LLM改进prompt.txt` 人工中间件：**删除**。
- `alpha_mining/knowledge/hub.py`：**保留**，不与 repository 重复；PublicExpressionGuard 继续承担公开表达式拥挤保护。

## 9. 实施步骤

1. 先写真实 DeepSeek fake transport 契约测试，复现 `invoke()` 错误。
2. 创建知识 repository 和完整性检测测试。
3. 修改 bridge 使用 `generate_json()`。
4. 扩展 CandidateProposal 和 Prompt。
5. 通过 S-002 catalog screening 后才接入 workflow。

## 10. 测试方案

- **T-401 修改前复现：** 注入真实协议 fake，仅提供 `generate_json()`，旧 bridge 抛 AttributeError 并回退。
- **T-402 根因级：** bridge 调用一次 `generate_json()`，schema 正确，最多 3 条。
- **T-403 模块集成：** `World quant` 片段内容真实出现在 user prompt 和 candidate refs。
- **T-404 端到端：** LLM 不可用时仅生成 1 个 fallback，并显示降级状态。
- **T-405 回归：** 缺失 inspiration 正文时 `KNOWLEDGE_INCOMPLETE`，不谎报数量。

## 11. 验收标准

- **A-401：** 全仓生产代码无 `self.llm.invoke(`。
- **A-402：** 每个模拟候选有可解析 knowledge refs 和 economic rationale。
- **A-403：** 日志准确区分 LLM 与 fallback。
- **A-404：** LLM 失败不会产生大批模板候选。

## 12. 风险和回滚

- 风险：知识片段过长导致 LLM 成本和噪声增加。
- 发现：Prompt 字符数和 snippet 数有固定上限测试。
- 回滚：降到确定性 fallback；不得恢复自由文本批量模板。

## 13. 明确禁止事项

- 不得仅断言“目录被读取”。
- 不得把全部 Markdown 无筛选拼进 Prompt。
- 不得声称不存在的 151 篇正文已加载。
- 不得在 LLM 失败后静默回退大量候选。

---

# S-005：修复 ArmTracker 编辑事故，接通 terminal feedback 与下一轮预算

## 1. 对应问题

- **问题编号：** P1-005
- **根因：** `stats()` 签名丢失、调用 API 错误、异常被吞、FeedbackStore 未接入、GenerationService 未使用反馈做预算。

## 2. 目标状态

1. 每个 terminal 请求，无论 PASS、NEAR_PASS、FAR_FAIL、FAILED、UNKNOWN、LOCAL_REJECTED，都有幂等 feedback。
2. 每条有效模拟调用 `record_observation()`；20 条窗口跨进程持久化并正确聚合。
3. feedback 会改变下一个周期的 family/direction 配额，而不只是排序。
4. 24 条高换手弱信号结果会使对应方向进入 RED/DEAD，不再继续占据同等预算。
5. feedback 写入失败会产生明确错误状态和测试失败，不能只打印 warning 后假装闭环正常。

## 3. 根因处理方式

首先恢复 `ResearchArmTracker.stats()` 和准确 observation 聚合；然后在 Orchestrator terminal 分支统一写 `CandidateFeedbackStore`；最后由 CandidateGenerationService 读取同一 feedback snapshot 生成硬预算。三层必须用同一个 `request_hash` 和 arm dimensions 连接。

## 4. 修改范围

- `alpha_mining/scheduler/arm_metrics.py`
- `alpha_mining/generation/feedback.py`
- `alpha_mining/generation/service.py`
- `alpha_mining/factory/orchestrator.py`
- `alpha_mining/factory/quality_workflow.py`
- `alpha_mining/filter/repair.py`
- `alpha_mining/storage/migrations.py`
- `tests/test_feedback_closed_loop.py`
- 扩展 `tests/test_authoritative_candidate_pipeline.py`
- `tests/fixtures/poor_quality_run.json`

## 5. 文件级修改说明

### `arm_metrics.py`

- 在孤儿代码前恢复：

```python
def stats(self, arm: ArmDimensions) -> ArmStats:
```

- `record_observation()` 不得把最后一条 Sharpe 和 bool 复制 20 次；必须持久化真实窗口聚合。
- `record_window()` 保留批量兼容，但生产调用改为 `record_observation()`。
- 新增明确 `ArmState`：YELLOW、RED、DEAD、GREEN。

### `feedback.py`

- 扩展 `record()` 接收完整 context、quality decision、correlation、operator topology 和 repair lineage。
- 新增 `feedback_snapshot()` / `family_summary()`，返回样本数、near/ready 数、median/max Sharpe、max Fitness、高换手弱信号比例和 distinct topology 数。
- first terminal write wins；UNKNOWN 不被 FAILED 覆盖。

### `orchestrator.py`

- SUCCESS、FAILED、UNKNOWN 和本地拒绝路径统一调用 feedback store。
- 成功后调用 `record_observation()`。
- 反馈失败不覆盖已确认外部结果，但必须记录 `FEEDBACK_WRITE_FAILED` factory event，并让周期状态 PARTIAL；测试中不得被无条件吞掉。

### `generation/service.py`

- 构造时必须获得 feedback provider，不再允许保存后不用。
- 根据 feedback snapshot 计算 family budget。
- DEAD family 候选数为 0；RED family 不得与正常 family 同等 round-robin。

### `repair.py`

- 只对 `QualityDecision.repairable=True` 的 NEAR_PASS 修复。
- 未识别失败不得默认 LOW_SHARPE，应为 `UNKNOWN_RESULT` 并停止自动修复。
- 高换手且 Sharpe/Fitness 同时低时直接 FAR_FAIL，不进行窗口雕参。
- 每次只允许一个 OFAT 变化，并保存 parent/child lineage。

## 6. 数据和状态变化

迁移 18 扩展 `research_arm_observation_windows`：

```text
current_window_count
current_window_base_pass_count
current_window_near_pass_count
current_window_sharpes_json
current_window_self_corr_pass_count
current_window_prod_corr_pass_count
current_window_final_submit_count
updated_at
```

- 每次 observation 在事务中更新窗口。
- 达到 20 条时将真实数组/计数 flush 到 `research_arm_metrics`，然后清零窗口。
- 重启后继续累计，不依赖内存 list。

冻结预算状态：

```text
YELLOW：有效样本 < 4，最多 1 个初始候选
RED：有效样本 >= 4，0 个 NEAR/READY，且 median Sharpe < 0.8 或高换手弱信号比例 >= 75%；权重 0.25，每 3 个周期最多探索 1 次
DEAD：有效样本 >= 8，0 个 NEAR/READY，max Sharpe < 0.8 且 max Fitness < 0.5；或 3 种不同 topology 均无改善；候选预算 0
GREEN：至少 1 个 READY，或持续 NEAR 且指标改善；正常预算，但仍受单周期 3 个初始候选总上限
```

只有 catalog hash、knowledge hash 变化或人工明确解除时，DEAD 才可重新探索。

## 7. 接口契约

```python
tracker.record_observation(
    arm,
    *,
    base_pass: bool,
    sharpe: float | None,
    near_pass: bool,
    self_corr_pass: bool,
    prod_corr_pass: bool,
    final_submit: bool,
) -> ArmStats
```

建议改为返回最新 stats，避免调用方再次查询。

```python
feedback.family_summary(strategy_family) -> FamilyFeedbackSummary
```

## 8. 旧逻辑处理

- 单条 `record_window()` 生产调用：**替换**。
- `try/except print warning`：**修改为有状态的附属失败处理**。
- CSV 历史反馈注册表：**停止调用并在 S-001 完成后删除**。
- 静态 candidate_score：**保留为同等 feedback 条件下的次级排序，不参与 family 生死决策**。

## 9. 实施步骤

1. 写精确复现 `AttributeError: stats` 的测试。
2. 恢复 `stats()` 方法签名，不改策略，先使现有测试通过。
3. 写 20 条不同 observation 的累计测试，证明不是复制最后一条。
4. 接入 CandidateFeedbackStore 全 terminal 路径。
5. 写 family summary 和 RED/DEAD 预算测试。
6. 接入 GenerationService。
7. 接入修复上限和 OFAT。
8. 用两天运行 fixture 验证 `arch_delta_liquid` 被停止。

## 10. 测试方案

- **T-501 修改前复现：** `record_window()` 最后抛 AttributeError。
- **T-502 根因级：** `stats()` 返回准确 ArmStats；20 条不同 Sharpe 的 median 正确。
- **T-503 模块集成：** Orchestrator 对 PASS/FAR/FAILED/UNKNOWN 均写一条 outcome，重复 finalize 不重复。
- **T-504 端到端：** 24 条 173% 换手率弱结果后，下一周期该 family 获得 0 候选或按冻结 RED/DEAD 规则限制。
- **T-505 回归：** 一个 family 失败不会永久停止整个生成循环；其他 family 继续获得预算。

## 11. 验收标准

- **A-501：** `ResearchArmTracker` 存在可调用 `stats()`。
- **A-502：** production 无单条 `record_window(sharpes=[...])` 调用。
- **A-503：** CandidateFeedbackStore 在所有 terminal 路径有调用证据。
- **A-504：** 第一周期反馈能改变第二周期实际候选数或顺序，不能只检查数据库权重。
- **A-505：** `arch_delta_liquid` fixture 不再获得 24/27 的有效 simulate 预算。

## 12. 风险和回滚

- 风险：惩罚过快导致探索不足。
- 发现：YELLOW/RED/DEAD 状态和触发证据可审计；保留小概率/新知识解除机制。
- 回滚：只回滚预算阈值，不回滚 feedback 持久化和 `stats()` 修复。

## 13. 明确禁止事项

- 不得只补一行 `def stats` 后宣称反馈闭环完成。
- 不得继续吞掉反馈异常。
- 不得把失败 family 仅排到列表后面但仍给相同配额。
- 不得让 UNKNOWN 立即重放。

---

# 4. 跨方案依赖与实施分组

## 4.1 依赖关系

```text
S-002 Catalog 硬校验
       ↓
S-004 LLM/知识候选只能输出可验证字段
       ↓
S-003 全指标质量决策
       ↓
S-005 terminal feedback 与预算
       ↓
S-001 唯一入口完成整链路接线和旧逻辑退出
```

说明：S-001 的入口骨架可以先创建，但在 S-002～S-005 完成前不得宣称可用，也不得删除旧脚本。

## 4.2 阶段 0：基线确认

### 进入条件

- 当前仓库可读取。

### 动作

1. 记录 branch、HEAD、工作区状态。
2. 运行 compileall、targeted 和 full pytest。
3. 重新解析 archive/simulate 数据，保存基线报告。
4. 确认旧入口实际调用图。
5. 创建 SQLite 备份。

### 退出条件

- 数字与诊断一致，或提交偏差报告；不得在数字冲突时直接实施。

## 4.3 阶段 1：S-002 与 S-003 的纯规则修复

先修 catalog validator 和 quality evaluator，因为后续生成和反馈都依赖这两个真实分类。

### 退出条件

- 所有无效表达式本地拒绝；硬门槛表驱动测试通过；无平台调用。

## 4.4 阶段 2：S-004 生成智能接通

修复 `generate_json`，建立知识仓库和候选引用。

### 退出条件

- LLM 契约测试通过；fallback ≤1；知识引用可追踪。

## 4.5 阶段 3：S-005 反馈闭环

先修 stats 编辑事故，再准确累计 observation，再接 outcome，再接预算。

### 退出条件

- 两周期测试证明实际候选预算发生变化。

## 4.6 阶段 4：S-001 唯一入口接线

创建最终入口、串行 workflow、READY CSV 和提交输入调整。

### 退出条件

- Fake Gateway 端到端通过；旧入口仍暂时保留但无权威文档调用。

## 4.7 阶段 5：授权真实 shadow 验收

默认不执行真实提交。用户授权后仅运行一个受控周期：初始候选≤3、总 simulate≤12、并发=1。

验收重点：

- 0 个本地可预防 HTTP 400；
- 无 recovery probe 并发冲突；
- 反馈即时写入；
- 下一周期预算变化；
- 不合格结果不进 CSV。

不要求本轮一定找到 READY Alpha。

## 4.8 阶段 6：旧逻辑清理

只有前述阶段全部通过才执行：

1. 提取历史 fixture。
2. 搜索 import、subprocess、PowerShell、文档引用。
3. 删除明确被替代的根目录入口和生成产物。
4. 重新运行全量测试和唯一入口扫描。

---

# 5. 测试与固定验收矩阵

## 5.1 固定测试文件

```text
tests/test_quality_generation_workflow.py
tests/test_single_generation_entrypoint.py
tests/test_catalog_screening_production.py
tests/test_quality_decision.py
tests/test_worldquant_knowledge_integration.py
tests/test_llm_consultant_contract.py
tests/test_feedback_closed_loop.py
tests/fixtures/poor_quality_run.json
```

## 5.2 必须保留并运行的现有回归

```text
tests/test_authoritative_candidate_pipeline.py
tests/test_factory_runtime_phase1.py
tests/test_factory_simulate_e2e.py
tests/test_candidate_exhaustion.py
tests/test_pending_request_drain.py（如当前仓库存在）
tests/test_offline_candidate_generation.py
tests/test_expression_identity.py
tests/test_llm_providers.py
tests/test_pipeline_loop_recovery.py（如当前仓库存在）
tests/test_authoritative_delivery_phase1.py
tests/test_description_pipeline_phase1.py
```

## 5.3 固定命令

```bash
python -m pytest -q tests/test_catalog_screening_production.py
python -m pytest -q tests/test_quality_decision.py
python -m pytest -q tests/test_llm_consultant_contract.py tests/test_worldquant_knowledge_integration.py
python -m pytest -q tests/test_feedback_closed_loop.py
python -m pytest -q tests/test_quality_generation_workflow.py tests/test_single_generation_entrypoint.py
python -m pytest -q tests/test_authoritative_candidate_pipeline.py tests/test_factory_runtime_phase1.py tests/test_factory_simulate_e2e.py tests/test_candidate_exhaustion.py tests/test_offline_candidate_generation.py tests/test_llm_providers.py
python -m compileall -q alpha_mining 生成Alpha.py 提交Alpha.py
python -m pytest -q
```

未执行的命令不得写 PASS。完整 pytest 收集失败不得写“主要测试通过”。

---

# 6. 追踪矩阵

| 问题编号 | 根因 | 方案编号 | 主要修改文件 | 测试编号 | 验收编号 |
|---|---|---|---|---|---|
| P1-001 | 数量填充、CSV 拼接、双主线 | S-001 | `生成Alpha.py`、`quality_workflow.py`、`orchestrator.py`、`service.py`、`ready_alpha_csv.py`、`提交Alpha.py` | T-101～T-105 | A-101～A-105 |
| P1-002 | production screening 未接 catalog validator | S-002 | `generation/validation.py`、`screening.py`、`platform/catalog.py`、`gateway.py` | T-201～T-205 | A-201～A-204 |
| P1-003 | Sharpe-only 分类与最终 Gate 混用 | S-003 | `quality/decision.py`、`orchestrator.py`、`feedback.py`、`migrations.py`、CSV/submit | T-301～T-305 | A-301～A-304 |
| P1-004 | LLM 协议错误、知识仅打印 | S-004 | `worldquant_repository.py`、`llm_consultant_bridge.py`、`service.py`、`consultant_generator.py` | T-401～T-405 | A-401～A-404 |
| P1-005 | stats 编辑事故、错误 observation API、feedback 未接预算 | S-005 | `arm_metrics.py`、`feedback.py`、`service.py`、`orchestrator.py`、`repair.py`、`migrations.py` | T-501～T-505 | A-501～A-505 |

检查结果：

- 每个冻结问题均有唯一方案。
- 每个方案均有根因测试、集成测试、端到端测试和回归测试。
- 不存在无问题来源的新增功能。
- Gateway response detail、probe 冲突和 archive 重复均已归入对应根因，不另造任务。

---

# 7. 允许修改清单

## 7.1 允许修改的文件和模块

```text
生成Alpha.py（新建）
提交Alpha.py（仅输入职责与禁止重复 simulate）
alpha_mining/config.yaml
alpha_mining/factory/quality_workflow.py（新建）
alpha_mining/factory/orchestrator.py
alpha_mining/factory/runtime.py
alpha_mining/generation/service.py
alpha_mining/generation/screening.py
alpha_mining/generation/validation.py
alpha_mining/generation/feedback.py
alpha_mining/generator/llm_consultant_bridge.py
alpha_mining/generator/consultant_generator.py
alpha_mining/generator/baseline_first.py
alpha_mining/knowledge/worldquant_repository.py（新建）
alpha_mining/quality/__init__.py（新建）
alpha_mining/quality/decision.py（新建）
alpha_mining/scheduler/arm_metrics.py
alpha_mining/filter/repair.py
alpha_mining/storage/migrations.py
alpha_mining/storage/ready_alpha_csv.py（新建）
alpha_mining/platform/catalog.py
alpha_mining/platform/gateway.py
本方案列出的测试和 fixture
唯一权威使用说明及直接调用旧入口的 PowerShell
```

## 7.2 条件允许修改的文件

- `alpha_mining/llm/deepseek.py`：只有当前 `generate_json` 契约无法满足已冻结 schema 时；不得改变鉴权和错误脱敏。
- `alpha_mining/factory/simulation_requests.py`：只有现有 context 不能保存 knowledge/repair lineage，且测试证明阻塞；优先使用现有 `context_json`。
- `alpha_mining/submitter/*`：原则上禁止；只有 READY CSV 字段映射导致现有 Guard 无法读取时，允许增加兼容读取，不得放宽任何 Gate。
- `auto_alpha_pipeline_rebuilt_v50.py`：只允许添加迁移适配所需的最小导出或修复明确被复用的函数；禁止全面重写。

## 7.3 验收后允许删除的文件

```text
生成高质量Alpha.py
批量simulate验证.py
生成Alpha_完全自动化.py
自动迭代闭环.py
迭代提交Alpha.py
LLM改进prompt.txt
archive_*.csv
simulate_results.csv
高质量Alpha候选.csv 及其 backup
alpha_generated_expressions.csv 的旧队列用途
直接启动以上旧入口的重复 ps1
过期且与唯一流程冲突的重复 README
```

删除仅限 Git 跟踪的代码/样例产物。真实用户运行数据必须先备份，且不得删除 `.gitignore` 排除的本地数据库、认证状态或用户未提交文件。

## 7.4 禁止修改的文件和模块

```text
alpha_mining/submitter/guard.py 的 Gate 语义
alpha_mining/submitter/delivery.py 的幂等提交语义
认证状态机和 browser login 流程
429/recovery 永久循环和 hard-stop 分类
描述生成、验证、PATCH 的安全链路
Git 历史和凭证清理任务
无关诊断脚本、代码风格和目录结构
```

---

# 8. 风险、回滚与偏差管理

## 8.1 总体风险

1. **真实平台门槛变化：** 使用“用户门槛与动态 Gate 取更严格值”处理。
2. **新流程过于保守导致 0 输出：** 这是允许状态，不得以此回滚质量门槛。
3. **知识和 LLM 噪声：** 通过 catalog、结构化 schema、最多 3 候选和 fallback≤1 控制。
4. **Feedback 过度惩罚：** 状态可审计，并允许知识/catalog 版本变化后重新探索。
5. **迁移损坏运行数据库：** 必须先 SQLite backup + integrity_check；迁移幂等。

## 8.2 回滚单位

每个 S-ID 必须独立 Commit；禁止把五个方案压在一个不可回滚的大 Commit 中。

建议 Commit 顺序：

```text
fix: enforce production catalog screening
fix: add complete alpha quality decision
fix: connect structured llm and worldquant knowledge
fix: restore arm metrics and feedback budgeting
feat: make quality generation workflow authoritative
chore: retire superseded generation entrypoints
```

## 8.3 偏差报告

实施时发现方案与代码冲突，必须停止对应 S-ID，并输出：

```text
偏差编号：D-xxx
冲突文件和行号：
冻结方案预期：
实际代码：
是否阻塞：
最小候选调整：
影响的测试和验收：
```

不得直接自行改动冻结范围。任何批准后的调整生成 `02-frozen-solution-v1.1.md`，不得覆盖本文件。

---

# 9. 方案冻结声明

## 9.1 冻结版本

```text
Frozen Solution Version: v1.0
```

## 9.2 冻结问题编号

```text
P1-001
P1-002
P1-003
P1-004
P1-005
```

## 9.3 冻结方案编号

```text
S-001
S-002
S-003
S-004
S-005
```

## 9.4 固定范围

- 唯一生成入口；
- 少量候选和固定模拟预算；
- 生产 catalog 本地硬校验；
- 全指标质量 Gate；
- LLM 结构化协议与 World quant 知识引用；
- terminal feedback、ArmTracker 和实际预算变化；
- 唯一 READY CSV；
- 验收后退出旧生成脚本。

## 9.5 固定验收标准

1. 初始候选≤3、周期 simulate≤12、并发=1。
2. UNKNOWN_FIELD/UNKNOWN_OPERATOR 在本地拦截且不调用平台。
3. 最终 READY 同时满足 Sharpe、Fitness、Turnover、相关性和 mandatory checks。
4. `LLMConsultantBridge` 不再调用 `invoke()`。
5. 每个候选有真实 knowledge refs。
6. 所有 terminal outcome 进入 CandidateFeedbackStore。
7. 反馈使下一周期实际候选预算变化。
8. `待提交Alpha列表.csv` 只含 READY。
9. `提交Alpha.py` 不重新承担候选 simulate。
10. 旧分裂入口退出生产调用，全量测试通过。

## 9.6 变更规则

冻结后：

- 不得新增未列出的问题；
- 不得新增未列出的重构；
- 不得替换技术方案；
- 不得调整硬门槛和预算来让测试或真实结果更好看；
- 不得以“顺便优化”为由扩大文件范围；
- 不得删除、skip 或弱化有效测试；
- 同一个失败最多自动尝试修复 3 次；
- 同类失败两次没有新证据时停止并提交偏差报告。

---

# 10. 最终实施结论

**方案已冻结，可以进入实施阶段。**

本版本只解决五个已经由代码和真实运行数据证明的阻塞问题。实施完成后的正确成功标准不是“保证立刻找到 Sharpe 大于 1.57 的 Alpha”，而是：

> 生成引擎只使用真实 catalog 和可追踪知识，以极少候选消耗平台预算；simulate 结果在同一流程立即形成全指标质量判断和持久化反馈；失败方向真实失去预算；只有满足全部提交门槛的 Alpha 才进入唯一待提交 CSV；达到这些标准后停止，不再启动新的全仓库重构。
