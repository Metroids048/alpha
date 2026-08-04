# 生成 Alpha 纯生产链路验收报告

日期：2026-08-04  
结论：**COMPLETE（本阶段纯生成验收通过；未执行平台 simulate）**

## 1. 修改前根因

`生成Alpha.py` 原先直接导入 `alpha_mining.factory.runtime`。旧运行时默认进入 `offline_main`，生产分支还会加载/同步平台目录、调用认证和 simulate，并把 READY 语义混入生成入口。`worldquant_repository._terms()` 只提取 ASCII token，真实中文规则不可检索。`CandidateCsvQueue.upsert()` 会覆盖消费端状态，无法支持待 simulate 队列。

## 2. 当前活动调用链

```text
生成Alpha.py
  -> alpha_mining.generation.production.main
  -> snapshots.load_local_snapshots (本地 catalog / SQLite / CSV)
  -> V50Kernel (ExpressionFactory / validator / history pools / novelty)
  -> WorldQuantKnowledgeRepository (本轮真实 Markdown 片段)
  -> DeepSeekStructuredLLM 阶段 A 研究计划
  -> DeepSeekStructuredLLM 阶段 B 表达式与批判
  -> high_quality 硬门槛与 local_quality_score
  -> CandidateCsvQueue 原子幂等写入 PENDING_SIMULATION
```

活动入口没有认证、浏览器、平台 client、simulate、check、submit 或 `offline.cli` 导入。

## 3. 修改文件职责与 v50 复用

- `生成Alpha.py`：薄入口，只指向 `production.main`。
- `alpha_mining/generation/snapshots.py`：固定目录顺序读取完整本地 catalog，汇总 SQLite、队列和 v50 CSV 反馈；缺失即 `CATALOG_UNAVAILABLE`。
- `alpha_mining/generation/v50_kernel.py`：以单一兼容边界适配 v50 `PipelineConfig`、`FieldCatalog`、`PreflightValidator`、`ExpressionFactory`、`HistorySimilarityPools`、`NoveltyIndex`、`NearPassAmplifier`；不实例化 `WorldQuantAlphaPipeline`。v50 文件仍整体被 import，见“未证明项”。
- `alpha_mining/generation/high_quality.py`：v50 seed 去重、World Quant 检索、两阶段 LLM、字段/算子/引用/数据集/相似度/窗口硬门槛和本地质量排序。
- `alpha_mining/generation/production.py`：CLI、单轮、常驻循环、脱敏日志和退出码。
- `alpha_mining/knowledge/worldquant_repository.py`：中文词、二元词片和中英别名；排除索引/认证/工程正文。
- `alpha_mining/storage/csv_queue.py`：请求级 schema、原子写入、事件 `GENERATED`/`LOCAL_REJECTED`/`DEDUPLICATED`/`LLM_UNAVAILABLE`/`ENQUEUED`、消费端状态保护；`GENERATED` 写入后再追加 `ENQUEUED`，事件有当前状态。
- `README.md`：明确当前 CSV 是待 simulate 队列，而不是 READY 提交队列。

旧 `factory.runtime`、`FactoryOrchestrator`、平台 gateway 和 v50 monolith 保留为回归基准，不再由新入口调用。

## 4. LLM、知识、反馈证据

定向 fake-transport 测试证明阶段 A prompt 含本轮真实 knowledge ref、字段/算子白名单、v50 seed 与反馈摘要；阶段 B provenance 写入 CSV，`knowledge_usage_mode=LIVE_LLM_KNOWLEDGE` 且 `degraded=false`。LLM 异常测试确认不会写 degraded 候选。

LLM 与本地生成异常已分开归类：模型调用异常为 `LLM_UNAVAILABLE`，内核/知识/本地校验异常为 `GENERATION_FAILED`，不会混淆运维诊断。

真实仓库 Markdown 测试命中：

- 自相关/相关性规则；
- 高质量 Alpha 工作流；
- 算子多样性与假说优先。

README、认证文档和 `Alpha灵感启示录.md` 不会作为 IDEA 正文引用。反馈测试写入 `SELF_CORRELATION` 与 `LOW_SHARPE` 后，下一轮会排除对应 v50 seed，且不会用未知 parent 继续入队。

## 5. v50 基线与新候选比较

| 指标 | v50 seed 基线 | 新链路测试候选 |
|---|---:|---:|
| 字段/算子白名单合法率 | 由 v50 预筛 | 100%（fake catalog 定向测试） |
| knowledge grounding | 无要求 | 100% |
| degraded 比例 | 可能存在旧 fallback | 0% |
| 精确/结构重复 | v50 行为基线 | 0%（队列与定向测试） |
| self-corr 代理 | v50 pool | `<0.65` 门槛 |
| local_quality_score | 不提供 | 入队阈值 75 |

本次生产 catalog 是本地历史平台观测重建快照，不是测试 fixture，也未联网补齐：
`research_memory.sqlite:alpha_expression_features` 提供 1001 个历史字段，4 个本地数据集映射，
`数据/本地运行产物/状态/.alpha_operators_cache.json` 提供 122 个 operator 名称。重建快照的 operator
arity 标记为不受信，生成链只严格校验 operator 名称、FASTEXPR 语法、字段存在和 dataset 边界；真实四文件
平台 catalog 恢复后仍应复核 arity 与字段描述。没有伪造 Sharpe/Fitness 或平台相关性比较。

## 6. 真实运行记录

脱敏环境检查：

- `DEEPSEEK_API_KEY_CONFIGURED=True`
- `DEEPSEEK_BASE_URL_CONFIGURED=True`
- `DEEPSEEK_MODEL_CONFIGURED=True`
- `DATABASE_EXISTS=True`
- 根目录三份 `.alpha_*` catalog：可读取，1001 fields / 122 operators / 4 datasets
- catalog source：`historical_platform_observations`，provenance=`research_memory.sqlite:alpha_expression_features`
- DeepSeek：`deepseek-chat`，真实 HTTP 200，未打印 key、请求头或完整 prompt

执行 `python 生成Alpha.py --once --candidates-per-cycle 1`：退出码 **0**；真实模型调用成功，
`knowledge=2`、`llm_candidates=5`、`rejected=10`、`enqueued=1`、`pending=1`。CSV 行满足
`PENDING_SIMULATION`、`alpha_id=""`、`degraded=false`、`knowledge_usage_mode=LIVE_LLM_KNOWLEDGE`、
`local_quality_score=96.0`。

执行 `python 生成Alpha.py --max-rounds 2 --interval 2 --candidates-per-cycle 2`：退出码 **0**，
观察到 `cycle_20260804T070216799939Z` 与 `cycle_20260804T070311422620Z`；第一轮读取已有队列并去重，
第二轮入队 2 条，最终有 3 条互不重复的 `PENDING_SIMULATION`。

无参数常驻冒烟由独立进程组驱动完成：`cycle_20260804T071132054170Z` 与
`cycle_20260804T071727938600Z` 两轮均完成并继续等待 300 秒；随后发送 `CTRL_BREAK_EVENT`，
生成进程正常退出且无 `.tmp` / `.lock` 残留。该过程无浏览器启动、无 `worldquantbrain.com` 请求。

## 7. 验证命令

- 定向 generation/knowledge/LLM/feedback/queue 测试：通过（11 generation/snapshot tests）。
- `C:\Users\win\.ai-workspace\venv\Scripts\python.exe -m pytest -q`：**通过，全部测试通过**。
- `C:\Users\win\.ai-workspace\venv\Scripts\python.exe -m compileall -q alpha_mining 生成Alpha.py`：通过。
- `git diff --check`：通过。
- 真实 DeepSeek 单轮、真实两轮命令、无参数两轮常驻冒烟：均有脱敏日志证据，退出/中断路径已验证。

## 8. 明确边界

本阶段没有 simulate，没有生成 alpha_id，没有修改 `提交Alpha.py` 的浏览器/提交行为。`local_quality_score` 不是 Sharpe 或 Fitness；平台通过率必须在下一阶段由 `提交Alpha.py` 的真实 simulate 验证。

## 9. 候选摘要与未证明项

CSV 当前有 4 条候选，均为 `LLM_REFINED_V50`、`PENDING_SIMULATION`、非 degraded、`alpha_id` 为空，
request/structure/behavior 三种签名均唯一。脱敏摘要：

| dataset | operator family | local_quality_score | self_corr_risk_score | 状态 |
|---|---|---:|---:|---|
| `pv1` | `group_neutralize>ts_delta` | 96.0 | 0.0 | `PENDING_SIMULATION` |
| `fundamental6` | `group_neutralize>ts_zscore>rank>ts_std_dev` | 100.0 | 0.0 | `PENDING_SIMULATION` |

尚未证明平台 Sharpe、Fitness、真实 self-correlation 或 submit 通过率；这些必须由下一阶段
`提交Alpha.py` 的真实 simulate 验证。重建 catalog 的来源和 arity 局限见第 5 节，不得把它解释为平台最新同步。

残余设计风险：为复用 v50 的已验证表达式工厂，`v50_kernel.py` 当前仍 import v50 单体模块；它没有实例化平台 pipeline，也没有发现平台请求，但 v50 顶层仍带 pandas bootstrap 代码。真实 catalog 恢复后，应在隔离环境复核该 import 在缺依赖环境下不会触发安装行为，再决定是否做更细粒度的纯能力提取。
