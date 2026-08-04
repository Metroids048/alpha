# 生成 Alpha 纯生产链路验收报告

日期：2026-08-04  
结论：**BLOCKED: CATALOG_UNAVAILABLE**

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
- `alpha_mining/generation/v50_kernel.py`：只适配 v50 `PipelineConfig`、`FieldCatalog`、`PreflightValidator`、`ExpressionFactory`、`HistorySimilarityPools`、`NoveltyIndex`、`NearPassAmplifier`；不实例化 `WorldQuantAlphaPipeline`。
- `alpha_mining/generation/high_quality.py`：v50 seed 去重、World Quant 检索、两阶段 LLM、字段/算子/引用/数据集/相似度/窗口硬门槛和本地质量排序。
- `alpha_mining/generation/production.py`：CLI、单轮、常驻循环、脱敏日志和退出码。
- `alpha_mining/knowledge/worldquant_repository.py`：中文词、二元词片和中英别名；排除索引/认证/工程正文。
- `alpha_mining/storage/csv_queue.py`：请求级 schema、原子写入、事件 `GENERATED`/`LOCAL_REJECTED`/`DEDUPLICATED`/`LLM_UNAVAILABLE`/`ENQUEUED`、消费端状态保护。
- `README.md`：明确当前 CSV 是待 simulate 队列，而不是 READY 提交队列。

旧 `factory.runtime`、`FactoryOrchestrator`、平台 gateway 和 v50 monolith 保留为回归基准，不再由新入口调用。

## 4. LLM、知识、反馈证据

定向 fake-transport 测试证明阶段 A prompt 含本轮真实 knowledge ref、字段/算子白名单、v50 seed 与反馈摘要；阶段 B provenance 写入 CSV，`knowledge_usage_mode=LIVE_LLM_KNOWLEDGE` 且 `degraded=false`。LLM 异常测试确认不会写 degraded 候选。

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

真实 catalog 缺失，因此没有伪造生产 Sharpe/Fitness 或平台相关性比较。

## 6. 真实运行记录

脱敏环境检查：

- `DEEPSEEK_API_KEY_CONFIGURED=True`
- `DEEPSEEK_BASE_URL_CONFIGURED=True`
- `DEEPSEEK_MODEL_CONFIGURED=True`
- `DATABASE_EXISTS=True`
- 根目录三份 `.alpha_*` catalog：缺失
- `数据/平台缓存/` 四份 catalog：缺失

执行 `python 生成Alpha.py --once --candidates-per-cycle 1`：退出码 **8**，日志明确为 `CATALOG_UNAVAILABLE`；未调用真实 LLM。

执行 `python 生成Alpha.py --max-rounds 2 --interval 2 --candidates-per-cycle 2`：观察到两个不同 cycle ID，第二轮正常等待后执行，退出码 **8**（catalog 阻塞），没有候选写入。

由于完整真实 catalog 不存在，无法证明真实 LLM 单轮成功、两轮产生至少两条 `PENDING_SIMULATION` 候选或常驻成功候选循环。

## 7. 验证命令

- 定向 generation/knowledge/LLM/feedback/queue 测试：通过。
- `C:\Users\win\.ai-workspace\venv\Scripts\python.exe -m pytest -q`：**通过，全部测试通过**。
- `... -m compileall -q alpha_mining 生成Alpha.py`：通过。
- `git diff --check`：通过。
- 两次真实入口命令：按上节结果退出 8，原因是环境目录缺失，不是静默 fallback。

## 8. 明确边界

本阶段没有 simulate，没有生成 alpha_id，没有修改 `提交Alpha.py` 的浏览器/提交行为。`local_quality_score` 不是 Sharpe 或 Fitness；平台通过率必须在下一阶段由 `提交Alpha.py` 的真实 simulate 验证。

## 9. 未证明项与最小下一步

阻塞点只有完整本地 datasets、data-fields、operators catalog 缺失。下一步应由运维动作导入真实本地快照，再重新运行真实 DeepSeek 单轮、两轮命令和常驻冒烟；不得把 `数据/导出结果/` 测试产物当作生产 catalog。
