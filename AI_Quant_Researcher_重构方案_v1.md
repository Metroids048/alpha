# AI Quant Researcher 重构方案
### 面向 Metroids048/alpha 仓库（WorldQuant BRAIN 自动化 Alpha 挖掘系统）
### 目标执行者：Claude Code / Codex（自主编码代理）
### 文档版本：v1.2（新增 1.4 登录限流根因诊断、Phase 0.5 登录加固方案、Master 编排 Prompt）

---

## 0. 本文档的使用方式

这不是一份要一次性丢给 Claude Code、指望它"一步重构完成"的超长 Prompt。8780 行的单体文件不是靠一次性大 Prompt 堆出来的（现有的 `auto_alpha_pipeline_rebuilt_v39` 到 `v50` 十余个版本已经证明了这条路径的结果：每次都是在同一份屎山上加参数、加阈值、加特判），我们不会重复这个模式。

正确做法：这份文档是**架构基准 + 分阶段任务清单**。你（使用者）按第 11 章的阶段顺序，把对应阶段的 Prompt 片段交给 Claude Code / Codex 执行，每个阶段都有明确的验收标准和回滚点。Claude Code 在开始任何一个阶段前，应先执行 `AGENTS.md` 里规定的动作（检索 `skills/**/SKILL.md`），并完整阅读本文档对应章节。

---

## 0.1 v1.1 更新说明：对照"九智能体 v3"方案

使用者随后提出了一版九智能体方案（Research Planner / Paper Reader / Hypothesis Generator / Knowledge Graph / Alpha Generator / Simulation Agent / Judge Agent / Memory / Income Optimizer）。逐个对照后的结论：

| 九智能体方案中的模块 | 对应本文档 v1.0 中的组件 | 处理方式 |
|---|---|---|
| Research Planner | L1 Idea Generator | 重合，采纳其"按数据类别分散（Price/Fundamental/Analyst/Sentiment）"的约束，补进 5.2 |
| Hypothesis Generator | L2 Hypothesis Generator | 完全重合，无变化 |
| Knowledge Graph (Memory) | Research Memory（SQLite） | 重合，但**不采纳"图数据库"这个具体实现**，理由见下方"未采纳"部分 |
| Alpha Generator | L4 Expression Generator（llm_grammar 策略） | 重合，采纳"显式注入历史最优算子 + 近期失败模式"这一条，补进 5.5 |
| Simulation Agent | 既有 async_batch.py / resilient_async.py | 完全重合，相互验证了"不要重写、保留 AI-free 的确定性引擎"这个判断 |
| Judge Agent | （v1.0 中没有对应物） | **新增**，见新增的 5.9 |
| Paper Reader | （v1.0 中只作为一次性人工种子来源） | **部分采纳**，见 5.1 的补充说明，但加了限制条件，理由见下方 |
| Income Optimizer | （v1.0 中没有对应物） | **新增，但列为后置阶段**，见新增的 5.11 与 Phase 7 |

三处**没有照单全收**，原因说清楚：

1. **Knowledge Graph 不建议真的上图数据库（如 Neo4j）。** 你现在的查询需求（"过去 Capex 相关的假设里哪个算子表现最好"）本质上是分组聚合查询，第 4 章的关系型 Schema（`hypotheses` / `data_mappings` / `expressions` 加外键）已经能表达同样的实体关系，用 SQL 的 JOIN + GROUP BY 就能回答。给一个人维护的项目多引入一套图数据库运维成本，在当前规模（几万条记录）下收益不明显。如果未来查询复杂到关系型表达不了（比如要做多跳的语义关联推理），再迁移不迟。

2. **Paper Reader 不建议做成全自动、无监督、每天自动抓取并直接生成假设写入采样池。** 原因是学术界公开因子里有相当一部分存在"数据挖掘偏差"（因子动物园 / factor zoo 问题：大量已发表异象在样本外无法稳定复现，这是量化研究里的老问题，不是我猜的）。如果 Paper Reader 每天自动往 Research Memory 里灌新假设，而且这些假设的初始采样权重和人工审阅过的假设一样高，噪音会被 Evolution Engine 当成信号一起学习，长期反而拖累系统。折中方案见 5.1：允许自动抓取和结构化抽取，但新假设默认进入"待验证"状态，用较低的初始采样权重跑一批小规模 simulate 拿到真实反馈后，再决定要不要提升权重——用平台真实结果做验证闸门，而不是直接信任论文标题。

3. **Income Optimizer 认可方向，但不建议现在就做，原因是训练数据本身的问题。** 你现在的规模是能提交 300–400 个，这个量级训练一个 XGBoost 去预测"未来收入"，样本量偏小、而且更关键的是——我不清楚你能拿到的"收入"标签具体是什么口径（WorldQuant 顾问的实际薪酬公式、alpha 的 quality multiplier、还是某种代理指标）。如果拿不到真实收入数据，用 Sharpe/Fitness 这类平台指标去拟合"收入"，本质上只是给现有指标加了一层不透明的非线性变换，不一定比直接看 Sharpe/Fitness/Self-Corr 三个指标更可靠，还可能因为过拟合小样本而产生误导性的排序。这个模块列为 Phase 7，等 Research Memory 积累出更大样本、且你确认了真实的收入信号来源之后再做，做法见 5.11。

---

## 1. 现状诊断（基于对仓库的实际走查，非猜测）

我读取了 `codex/v50.4-pipeline-recovery` 分支的完整文件树和关键源码，结论如下：

### 1.1 核心事实

| 项目 | 实际情况 |
|---|---|
| 主逻辑 | 单文件 `auto_alpha_pipeline_rebuilt_v50.py`，**8780 行**，10 个类、约 100 个函数 |
| 历史版本 | 仓库根目录同时存在 v39/v40/v41/v42/v43/v44/v46/v47/v48/v49/v50 共 11 个完整版本文件，从未清理 |
| 模块化尝试 | `alpha_mining/` 包已经搭了骨架（`generator/` `mutate/` `filter/` `parser/` `submitter/`），但除 `simulate/`、`storage/sqlite_store.py`、`scheduler/queue_probe.py` 外，**其余全部是 1 行的空 `__init__.py` 占位符**。`alpha_mining/main.py` 甚至还在 `import` 一个不存在的 `auto_alpha_pipeline_rebuilt_v34`——说明这次模块化重构本身就已经烂尾、且没人验证过它能不能跑。 |
| Idea/Hypothesis 层 | **完全不存在。** 表达式生成来自 `ExpressionFactory` 类里硬编码的 12 个"考古学模板"函数：`_arch_A_fundamental_ts_rank` ... `_arch_L_multi_field`，外加一个 `_alpha_models_template_family`。每个模板固定了字段池 + 算子结构，只随机化窗口参数。这**正是**你之前收到的建议里说的"Idea Space 太小"问题——不是理论推测，是可以在代码里直接数出来的：**生成侧的"假设"数量上限就是这 13 个模板**，量再大也是同一批模板的参数重排列。 |
| LLM 调用 | 在整个 `auto_alpha_pipeline_rebuilt_v50.py`（8780 行）里**搜索 openai/anthropic/gpt/claude/ChatCompletion 关键字，零命中**。也就是说，运行时的 alpha 生成**没有任何一次调用大模型**——LLM 目前只是被用来"写代码"，从未被用来"做研究"。这是最大的结构性缺口，也是你之前收到的两份分析笔记里反复强调的核心问题在代码层面的直接证据。 |
| 多样性控制 | 存在，但停留在"语法"层：`HistorySimilarityPools` / `NoveltyIndex` / `_structure_signature` / `_token_jaccard` 用 token Jaccard 相似度做去重。这是一个**可以复用、值得保留**的雏形，但它只能判断"两个表达式写法像不像"，判断不了"两个 alpha 背后的经济学假设像不像"——所以历史相关性问题只能被延后，不能被根治。 |
| 失败修复 | 存在雏形：`_classify_feedback_reason`、`_feedback_analysis_fields`、`NearPassAmplifier`、`PreSimulationScreener`、`HopefulQueue`。这些已经是"失败分类 → 近似修复"的早期版本，**不需要推倒重来**，需要的是把它们从"字符串关键字匹配"升级成结构化的失败分类体系。 |
| 提交与并发引擎 | `alpha_mining/simulate/async_batch.py`（776 行）+ `resilient_async.py`（296 行）是这个仓库里工程质量最高的部分：`aiohttp` 并发、429 限速自适应、`NetworkCircuitBreaker` 熔断、死信队列 `dead_letter`。**这部分不要动**，新架构应该复用它，而不是重写。 |
| Research Memory | 名义上存在（`总alpha.csv` 6.9 万行、`通过门槛的alpha.csv`、`alpha_mining_smoke.sqlite3`、`alpha_novelty_store.json`），但 schema 只到 `expression / family / sharpe / fitness / turnover / ...` 这一层——**没有 hypothesis、没有 topic、没有 data mapping 理由、没有 mutation 血缘、没有 embedding**。是一份"结果日志"，不是"研究记忆"。 |
| 领域知识沉淀 | `worldquant_brain_submission_skill.md` 是一份手写的静态知识文档（Family A–F、8 条硬约束、8 个 few-shot 例子）。内容质量不错，但它是**静态文件**，不会随着真实提交结果自动更新、也不会被生成器在运行时结构化查询——本质上是"写死在文档里的知识图谱雏形"，应该被结构化并接入 Research Memory，而不是继续躺在 md 文件里。 |
| 配置 | `PipelineConfig` 里有大量手工调过的魔法数字，例如注释 `# v50: was 0.72`、`# v50.1: was 2000 — 只保留最明确的失败案例`。这些注释本身就是证据：过去十个版本的迭代方式是"改阈值试错"，而不是"改结构"。边际收益递减是必然结果，这与你之前收到的分析判断完全吻合。 |

### 1.2 一个需要立刻处理的安全问题（与架构无关，但优先级最高）

`modelswitch.py` 中硬编码了一个看起来是真实可用的 API Key：

```python
OPENAI_API_KEY = "sk-69e64ade31e34ec59813c381e9cb2cfb"
```

这个文件已经提交到 **公开的 GitHub 仓库**（`Metroids048/alpha`，Public repo）。任何人都可以直接复制这个 key 去消耗你的额度/产生费用。这个问题必须在做任何架构工作之前先处理，见第 10 章"Phase 0"。同时建议检查 `.env.example`、`fix-codex-proxy.ps1`、`setup-chatgpt-network.ps1`、`chatgpt-proxy.pac` 等文件是否也含有类似硬编码凭证。

### 1.3 仓库卫生问题（次优先级，但会拖慢 Claude Code 的每一次任务）

- 根目录同时躺着 11 个历史版本的主文件（合计几百 KB 到 400+ KB 不等），`总alpha.csv` 69MB、`pipeline_loop.log` 4.6MB、`alpha_mining_smoke.sqlite3` 已提交进 git——这些体积会拖慢 Claude Code 每次读取仓库上下文的速度，也会让 `grep`/搜索类操作返回大量噪音。
- `skills/` 目录下混入了大量与本项目无关的第三方 skill（`react-best-practices`、`pptx`、`pm-*` 系列、`ui-ux-pro-max` 等），根据 `AGENTS.md` 的规则，Claude Code 每次开工前都要扫描 `skills/**/SKILL.md`——目前这个目录里 90% 以上内容和 Alpha 挖掘无关，纯粹增加搜索成本和误触发风险。

---

### 1.4 今天实际触发的紧急问题：登录被平台限流（根因已在代码里定位）

错误 `You may have exceeded the number of sign-ins allowed today` 是 WorldQuant BRAIN 平台对 `/authentication` 接口的每日调用次数限制。我在代码里定位到了具体原因，不是网络问题，是设计问题：

1. **`authenticate()` 没有任何"会话是否还有效"的判断，每次被调用都无条件重新登录**。全仓库搜索 `self.authenticate()` 的调用点，命中 **10 处**（`auto_alpha_pipeline_rebuilt_v50.py` 里的 `run_generate`、`run_submit_queue`、`run_preflight`、`run_analyze_recent` 等几乎每一个顶层命令开头都会先调一次 `self.authenticate()`），没有一处先检查"当前 session 是否还没过期"。
2. **存在两条完全独立的登录路径**：同步的 `requests.Session`（`WorldQuantAlphaPipeline.authenticate()`，主文件第 5417 行）和异步的 `aiohttp.ClientSession`（`alpha_mining/simulate/async_batch.py` 里的 `_authenticate()`，第 163 行），各自独立向 `/authentication` 发请求。如果一个 cycle 里先跑了同步 preflight 检查、又跑了异步批量 simulate，一次 cycle 就可能产生 2 次独立登录，而不是复用同一个已登录会话。
3. **`authenticate()` 本身默认重试 12 次**（`--auth-retries` 参数 help 文本写明"default 12"），并且在 SSL/连接错误时会调用 `_rebuild_http_session()` 整个重建 HTTPS 会话（新的 TLS 上下文），重建后大概率会再次触发登录——网络稍微抖动一下，一次 `authenticate()` 调用背后可能是好几次实际的登录请求，而不是同一次登录的无害重试。
4. **`run_pipeline_supervisor.py` 是一个崩溃自动重启的看门狗**，默认 `--max-restarts 200`、`--restart-sleep 90`（秒）。它每次重启都是启动一个全新的子进程，而登录状态只存在内存里的 `self.sess`，**没有任何跨进程持久化**——所以只要进程崩溃重启一次，就等于又要重新登录一次。仓库里已经有一个 `work/codex_crash_forensics_20260712_000607/` 目录，说明之前确实发生过崩溃排查——如果那次崩溃触发了持续重启，短时间内产生几十上百次登录请求是完全可能的，这和"exceeded number of sign-ins allowed today"这个报错完全对得上。

结论：**这不是运气不好，是当前实现里"登录"被当成一个无状态、可以随便重复调用的操作，而实际上它是有平台配额的敏感操作**。修复方案见新增的 12 章（Phase 0.5），会作为最高优先级、独立于第 2–11 章的架构重构单独处理，因为它现在是真正阻断你正常使用的问题。

## 2. 重构的根本判断

**认同**你之前收到的两份分析笔记的核心结论：真正的瓶颈不是"能不能生成更多 Expression"，而是"背后有多少个真正不同的、可验证的研究假设（Hypothesis）"。当前代码把这件事量化得很清楚：**13 个硬编码模板 = 13 个假设上限**，不管每天跑出多少万条 Expression，统计意义上的自由度就是这 13 个模板 × 参数组合。这解释了为什么相关性会越来越高、为什么边际收益会递减。

但**不认同**"把 Prompt 写到 2500~5000 行丢给 Claude Code 让它一次性重构"这个执行路径。理由：

1. 这是一个**每天在跑真实 simulate/submit、有真实 track record 的生产系统**。8780 行单体一次性推倒重来，出问题时你无法定位是哪一层坏的，而且会打断你作为顾问的实际产出节奏。
2. 过去 11 个版本的历史已经证明："让 AI 在同一份代码上不断打补丁"这个模式本身就是问题的一部分，而不是解法。
3. 好的做法是 **Strangler Fig（绞杀者模式）**：新架构作为独立模块在旁边搭建、先在 dry-run/离线模式下验证质量，确认新层产出的候选池比旧的 12 个模板更优之后，再逐步把流量切过去，最后再退役旧模板——而不是删除重写。
4. `alpha_mining/` 包的目录结构（`generator/mutate/filter/parser/submitter/scheduler/storage`）已经是一个合理的目标分层，只是里面是空的。**新架构应该真正把这些空文件填满**，而不是在根目录再造一个 v51 monolith。

---

## 3. 目标架构总览

```
                         ┌─────────────────────────────┐
                         │        Knowledge Layer       │
                         │  (Field Ontology / Factor    │
                         │   Library / Passing-Sample    │
                         │   Rules / Papers)             │
                         └───────────────┬───────────────┘
                                         │
                         ┌───────────────▼───────────────┐
                         │        Research Memory         │
                         │  (SQLite：topics/hypotheses/    │
                         │   mappings/expressions/         │
                         │   mutations/simulations/        │
                         │   repairs/embeddings)           │
                         └───────────────┬───────────────┘
                                         │  (读：采样概率；写：每一步结果)
        ┌────────────────────────────────┼────────────────────────────────┐
        │                                │                                │
┌───────▼────────┐   ┌───────────▼──────────┐   ┌───────────▼──────────┐
│ L1 Idea         │──▶│ L2 Hypothesis         │──▶│ L3 Data Mapping        │
│ Generator       │   │ Generator             │   │                        │
│ (研究主题采样)   │   │ (主题→可证伪假设)      │   │ (假设→候选 DataField 集)│
└─────────────────┘   └───────────────────────┘   └───────────┬───────────┘
                                                                │
        ┌────────────────────────────────────────────────────┘
        │
┌───────▼────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│ L4 Expression   │──▶│ L5 Mutation Engine      │──▶│ Diversity Gate         │
│ Generator       │   │ (树状变异，非随机)       │   │ (embedding 相似度拒绝) │
│ (含现有 arch_*  │   │                         │   │                        │
│  模板 + LLM 生成)│   │                         │   │                        │
└─────────────────┘   └───────────────────────┘   └───────────┬───────────┘
                                                                │
                                            ┌───────────────────▼───────────────────┐
                                            │  既有 Simulation/Submission 引擎         │
                                            │  (async_batch.py / resilient_async.py    │
                                            │   —— 原样复用，不重写)                    │
                                            └───────────────────┬───────────────────┘
                                                                │
                                            ┌───────────────────▼───────────────────┐
                                            │  L6 Repair Engine                       │
                                            │  (结构化失败分类 → 修复策略 → 回灌 L4/L5)  │
                                            └───────────────────┬───────────────────┘
                                                                │
                                            ┌───────────────────▼───────────────────┐
                                            │  Evolution Engine（周期性任务）           │
                                            │  用 Research Memory 里的成功率，          │
                                            │  更新 L1/L4 的采样概率                    │
                                            └───────────────────────────────────────┘
```

> **v1.1 补充**：图中"既有 Simulation/Submission 引擎"和"L6 Repair Engine"之间，实际还有一层 **Submission Judge**（详见 5.9）——平台门槛通过之后，不是直接按 Sharpe 排序提交，而是先做一次多样性/覆盖面打分再决定提交顺序，这一层是为了应对"通过门槛 1000+ 条但受相关性预算限制只能提交 300–400 条"这个真实的筛选问题。此外，Knowledge Layer 上游可以再加一个可选的、周期性的 Paper Reader（详见 5.1.1），Evolution Engine 旁边未来可选接入 Income Optimizer（详见 5.11，Phase 7 才做）。这两处在图上没有画出来，是为了不让核心链路图变得过于复杂，具体位置见对应章节。

关键设计原则：

1. **Research Memory 是唯一的真相来源（single source of truth）**。所有层读写都通过它，不允许层与层之间直接传参绕过记忆库——这样 Evolution Engine 才有数据可学。
2. **L4 不是替换现有 `ExpressionFactory`，是扩展它**。现有 12 个 `_arch_*` 模板保留、继续跑，但被重新定义为"L4 的一种生成策略（模板策略）"，与新增的"LLM 语法引导生成策略"并列，两者产出都要写回 Research Memory，用同一套指标横向比较，谁产出的通过率/多样性高，Evolution Engine 就把采样权重往谁那边调——**用数据决定去留，而不是主观替换**。
3. **Diversity Gate 前移到 simulate 之前**，而不是像现在这样在 simulate 完之后靠平台的 self-correlation 检查反馈——省 simulate 配额，这点你之前的分析笔记里也强调过（"不用等 WorldQuant 告诉你 Correlation"）。

---

## 4. Research Memory：数据库 Schema

在 `alpha_mining/storage/sqlite_store.py` 现有的 `simulation_runs` 表基础上**扩展**（不删除旧表，做兼容迁移），新增以下表：

```sql
-- 研究主题：Idea Generator 的采样池
CREATE TABLE IF NOT EXISTS research_topics (
    topic_id        TEXT PRIMARY KEY,       -- 例如 "capital_efficiency"
    topic_name_cn   TEXT NOT NULL,
    topic_name_en   TEXT NOT NULL,
    category        TEXT,                   -- profitability/growth/value/quality/liquidity/...
    data_category   TEXT,                   -- price/fundamental/analyst/sentiment/options/hybrid（v1.1 新增，供 5.2 覆盖约束使用）
    description     TEXT,
    source          TEXT,                   -- seed / llm_generated / paper_derived
    created_at      TEXT NOT NULL,
    active          INTEGER DEFAULT 1
);

-- 研究假设：Hypothesis Generator 的输出
CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id   TEXT PRIMARY KEY,
    topic_id        TEXT NOT NULL REFERENCES research_topics(topic_id),
    statement_cn    TEXT NOT NULL,           -- "资本效率持续改善 → 未来盈利能力增强 → 股价可能低估"
    statement_en    TEXT,
    mechanism       TEXT,                    -- 经济学/行为学机制的一句话解释，用于可解释性与人工审阅
    horizon         TEXT,                    -- short/medium/long，用于匹配 decay/delay 设置
    embedding       BLOB,                    -- 假设文本的向量表示，用于语义去重
    created_at      TEXT NOT NULL,
    llm_model       TEXT,                    -- 生成该假设所用的模型标识
    status          TEXT DEFAULT 'active'    -- active/retired/merged
);

-- 假设 -> DataField 候选映射（一对多）
CREATE TABLE IF NOT EXISTS data_mappings (
    mapping_id      TEXT PRIMARY KEY,
    hypothesis_id   TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
    data_field      TEXT NOT NULL,           -- 例如 "roic"
    dataset_id      TEXT,
    rationale       TEXT,                    -- 为什么这个字段能代表该假设
    field_quality_score REAL,                -- 复用现有 field_quality_score() 逻辑
    selected_by     TEXT,                    -- llm / rule / manual
    created_at      TEXT NOT NULL
);

-- 表达式（扩展现有 simulation_runs，增加血缘与来源字段）
CREATE TABLE IF NOT EXISTS expressions (
    expression_id     TEXT PRIMARY KEY,
    expression_text   TEXT NOT NULL,
    normalized_text   TEXT NOT NULL,          -- 复用现有 _normalized_expression()
    structure_sig      TEXT,                  -- 复用现有 _structure_signature()
    hypothesis_id      TEXT REFERENCES hypotheses(hypothesis_id),   -- 可为空（兼容旧的模板策略产物）
    parent_expression_id TEXT REFERENCES expressions(expression_id), -- 变异血缘
    generation_strategy TEXT NOT NULL,        -- "template_arch_A" / "llm_grammar" / "mutation"
    generation_layer    TEXT NOT NULL,        -- L4 / L5
    embedding           BLOB,                 -- 语义向量（非 token 级）
    created_at           TEXT NOT NULL
);

-- 变异树：记录每一次变异的轴与父子关系
CREATE TABLE IF NOT EXISTS mutations (
    mutation_id       TEXT PRIMARY KEY,
    parent_expression_id TEXT NOT NULL REFERENCES expressions(expression_id),
    child_expression_id  TEXT NOT NULL REFERENCES expressions(expression_id),
    mutation_axis     TEXT NOT NULL,          -- operator/window/normalization/neutralization/composite
    mutation_detail   TEXT,                   -- 例如 "window 126 -> 63"
    created_at        TEXT NOT NULL
);

-- 模拟结果：在现有 simulation_runs 基础上扩展外键，而不是重建
ALTER TABLE simulation_runs ADD COLUMN expression_id TEXT REFERENCES expressions(expression_id);
ALTER TABLE simulation_runs ADD COLUMN region TEXT;
ALTER TABLE simulation_runs ADD COLUMN universe TEXT;
ALTER TABLE simulation_runs ADD COLUMN neutralization TEXT;
ALTER TABLE simulation_runs ADD COLUMN decay INTEGER;
ALTER TABLE simulation_runs ADD COLUMN delay INTEGER;
ALTER TABLE simulation_runs ADD COLUMN correlation_max REAL;

-- 修复历史：结构化失败分类与修复策略
CREATE TABLE IF NOT EXISTS repairs (
    repair_id         TEXT PRIMARY KEY,
    expression_id     TEXT NOT NULL REFERENCES expressions(expression_id),
    failure_category  TEXT NOT NULL,          -- 见第 8 章失败分类表
    failure_detail    TEXT,
    repair_strategy   TEXT NOT NULL,
    resulting_expression_id TEXT REFERENCES expressions(expression_id),
    success           INTEGER,                -- 修复后是否通过
    created_at        TEXT NOT NULL
);

-- Paper Reader 抽取的待验证假设（v1.1 新增，不直接进 hypotheses 主表，见 5.1.1）
CREATE TABLE IF NOT EXISTS hypotheses_staging (
    staging_id      TEXT PRIMARY KEY,
    topic_id        TEXT REFERENCES research_topics(topic_id),
    statement_cn    TEXT NOT NULL,
    mechanism       TEXT,
    source_url      TEXT,                    -- 论文/Learn 页面来源
    review_status   TEXT DEFAULT 'pending',   -- pending / promoted / rejected
    trial_pass_rate REAL,                     -- 小批量试跑后的真实通过率
    created_at      TEXT NOT NULL
);

-- expressions 表新增字段，供 Submission Judge（5.9）使用（v1.1 新增）
ALTER TABLE expressions ADD COLUMN submission_priority_score REAL;
ALTER TABLE expressions ADD COLUMN novelty_score REAL;

-- 主题/假设级别的滚动统计（Evolution Engine 直接读这张表，避免每次现算）
CREATE TABLE IF NOT EXISTS topic_stats (
    topic_id          TEXT PRIMARY KEY REFERENCES research_topics(topic_id),
    total_generated   INTEGER DEFAULT 0,
    total_simulated   INTEGER DEFAULT 0,
    total_passed_gate INTEGER DEFAULT 0,
    total_submitted   INTEGER DEFAULT 0,
    pass_rate         REAL DEFAULT 0,
    avg_sharpe        REAL,
    avg_fitness       REAL,
    avg_self_corr     REAL,
    sampling_weight   REAL DEFAULT 1.0,       -- Evolution Engine 更新的字段
    last_updated      TEXT
);
```

**关于历史数据回填**：`总alpha.csv`（6.9 万行）和 `通过门槛的alpha.csv` 里已经有 `family` 字段，这是可以映射到 `generation_strategy` 的现成数据。Phase 1 的一部分工作就是把这两个 CSV 灌入新 schema（`expressions` + `simulation_runs`），这样 Evolution Engine 从第一天起就有历史统计可用，而不是从零开始学习。

---

## 5. 各层详细设计

### 5.1 Knowledge Layer

来源（按优先级）：
1. `worldquant_brain_submission_skill.md` 里已经写好的 Family A–F、8 条硬约束、8 个 few-shot 例子 —— 结构化拆解后写入 `research_topics` + 硬约束表（新增 `hard_constraints` 表，字段：`constraint_id/description/check_type/applies_to_layer`）。
2. 平台 Data Fields / Operators 文档（现有 `FieldCatalog` 类已经在抓取字段元数据，复用它，不重新写爬虫）。
3. `ssrn-2701346.pdf`（仓库里已有的论文）—— 用 `pdf-reading` skill 提取，人工审阅后转成 1-2 个 `research_topics` + 对应 `data_mappings` 候选，作为知识图谱的种子，不建议让 LLM 无监督地大规模"读论文批量生成假设"，容易引入噪音假设污染 Memory。
4. 经典因子文献（Value/Momentum/Quality/Low-Vol/Accrual 等公开因子分类）——作为 `research_topics` 的初始种子清单（建议 15–25 个 topic，对应你之前笔记里列的：盈利能力/成长/价值/估值/资本开支/盈利质量/资产效率/流动性/资金行为/分析师预期/行业轮动 等）。

**5.1.1 Paper Reader（半自动，v1.1 新增）**

允许做成一个定期任务（不必每天，建议每周），自动抓取 SSRN / Arxiv / WorldQuant Learn 的新内容，用 LLM 抽取 `{hypothesis_statement, mechanism, candidate_data_concepts}`，但**不直接写入 `hypotheses` 主表**，而是写入一张新增的 `hypotheses_staging` 表（字段与 `hypotheses` 相同，另加 `source_url` / `review_status`）。这些 staging 假设：
- 初始 `sampling_weight` 设为全局默认值的 0.3 倍左右（具体系数可调），不会一开始就跟人工审阅过的假设抢同样的采样机会；
- 跑过至少一轮小批量 simulate（建议 20–30 条表达式）之后，根据真实 `pass_rate`/`avg_sharpe` 表现，才允许 Evolution Engine 把它转正、并入 `hypotheses` 主表参与正常权重更新；
- 表现明显差的（比如多轮 simulate 后 pass_rate 为 0）标记为 `review_status = 'rejected'`，不再消耗生成配额。

这样做的原因：公开发表的因子里有不少存在样本外复现失败的问题（factor zoo / 数据挖掘偏差是量化研究里被反复讨论的现象），全自动无监督地把论文结论当真理直接写入长期记忆，噪音会被后面的 Evolution Engine 当成信号一起学习，长期是负资产。用真实 simulate 结果做验证闸门，比信任论文标题更可靠。

### 5.2 L1 — Idea Generator（Research Planner）

- 输入：`research_topics` 表（`active=1`）+ `topic_stats.sampling_weight`。
- 逻辑：按权重采样一个或多个 topic，而不是均匀随机——这就是"哪些 Hypothesis 成功率最高就多生成哪些"的落地方式。
- **数据类别覆盖约束（v1.1 新增）**：`research_topics` 增加一个 `data_category` 字段（枚举：`price` / `fundamental` / `analyst` / `sentiment` / `options` / `hybrid`），L1 在采样时除了按 topic 权重采样，还要保证**每一轮生成的 topic 组合覆盖至少 3 个不同的 `data_category`**，避免权重收敛后系统只在一个数据类别里反复深挖（比如只挖 fundamental，完全不碰 analyst/sentiment），这是对纯权重采样的一个硬约束修正，你提到的"今天研究 Fundamental×Price / Quality / Accrual / Capex / Sentiment"这种跨类别铺开的诉求就是靠这条约束实现的。
- 冷启动阶段（`topic_stats` 数据不足）：均匀采样 + 强制探索（epsilon-greedy，建议 epsilon=0.2，保证新 topic 有机会被验证，不会被早期噪音锁死）。**注意：你现在已经有约 2 万条历史 alpha 数据，Phase 1 回填之后 `topic_stats` 不会是真正意义上的冷启动，epsilon 可以从一开始就设得更低（比如 0.1），更快进入按权重采样的阶段。**
- 输出：`{topic_id}` 传给 L2。

### 5.3 L2 — Hypothesis Generator（LLM 驱动，结构化输出）

这是新增的第一个真正调用 LLM 的地方。给 Claude Code 的实现要求：

- 用结构化输出（JSON schema），不要自由文本，避免下游解析出错：

```json
{
  "hypothesis_statement": "string, 一句话陈述",
  "mechanism": "string, 为什么这个假设可能成立的机制",
  "horizon": "short | medium | long",
  "expected_direction": "string, 例如 factor 上升 -> 未来收益上升",
  "candidate_data_concepts": ["string", "..."]
}
```

- Prompt 必须把 `research_topics.description` + 该 topic 下**已经存在的** `hypotheses.statement` 列表一起喂给模型，并明确要求"生成一个与已有列表语义上不同的新假设"——防止同一个 topic 下反复生成换皮版本的同一个假设。
- 生成后立刻计算 embedding，与 `hypotheses` 表里全部历史 embedding 做 cosine 相似度检查，超过阈值（建议 0.90 起步，可调）直接丢弃重生成，不写入表。这一步比现在的 token Jaccard 更贴近"语义查重"。

### 5.4 L3 — Data Mapping

- 输入：一条 `hypothesis` 记录。
- 逻辑：LLM 从 `FieldCatalog`（复用现有类，不重写）提供的候选字段池里，为该假设挑选 3–8 个候选 `data_field`，并为每个字段写一句 `rationale`。
- 关键约束：**候选池必须先由 `FieldCatalog` + 现有的 `is_bad_field_name` / `is_weak_fundamental_field` / `field_quality_score` 函数过滤过一遍**，LLM 只能在"已知质量尚可"的字段里选，不允许自由生成字段名（防止幻觉字段名导致 simulate 直接报错，浪费配额）。
- 输出写入 `data_mappings` 表，每条记录都可回溯"这个字段是因为哪个假设、哪个理由被选中的"——这是现有系统完全没有的可解释性。

### 5.5 L4 — Expression Generator（扩展，不替换）

两条并行的生成策略，产出都写入统一的 `expressions` 表，用 `generation_strategy` 字段区分：

1. **`template_arch_*`**：现有 `ExpressionFactory._arch_A_fundamental_ts_rank` 到 `_arch_L_multi_field` 原样保留，作为"策略之一"继续跑，但**必须补上 `hypothesis_id = NULL`**（因为模板策略本来就不经过假设层，这是诚实的记录，不是缺陷）。
2. **`llm_grammar`**（新增）：LLM 拿到 L3 输出的 `data_mappings`，在一套受限文法（复用 `PreflightValidator` 里已有的算子白名单和语法校验逻辑）内生成表达式，必须通过 `_quality_gate()` 才写入表。**不允许 LLM 自由生成任意字符串直接丢去 simulate**——受限文法 + 现有 validator 双重把关，避免语法错误浪费 simulate 配额。

**Prompt 构造要求（v1.1 补充）**：调用 LLM 生成表达式之前，Prompt 里必须显式拼入两类历史信息，而不是只给 `data_mappings`：
- 从 `expressions` + `simulation_runs` 里查出**同一 `data_field` 或同一 `mutation_axis` 下历史表现最好的算子组合**（例如"过去 capex_to_total_assets 字段配合 `group_rank` + `ts_delta(252)` 的通过率最高"），作为正向参考注入 Prompt；
- 从 `repairs` 表里查出**最近一批同类型失败的具体原因**（例如"最近 20 条 SELF_CORRELATION 失败集中在 subindustry 中性化 + 短窗口组合"），作为负向约束注入 Prompt，明确要求"不要生成落入以下失败模式的表达式"。

这两点本质上是让 L4 每次生成前都先查一遍 Research Memory，而不是每次都拿一份不变的静态 Prompt 模板——这是把"记忆"真正用起来的地方,而不是只存不用。

两种策略产出的表达式，后续走同一套 Diversity Gate → Mutation → Simulate 流程，没有优先级差异，让数据自己说话。

### 5.6 L5 — Mutation Engine（树状，非随机）

对已经通过 simulate 或"接近通过"（复用现有 `HopefulQueue` / `NearPassAmplifier` 的判定逻辑）的表达式，系统性地沿 5 个轴各生成 1–3 个变体，而不是随机变异：

| 变异轴 | 示例 |
|---|---|
| operator | `ts_rank` ↔ `ts_zscore` ↔ `winsorize` |
| window | `126` ↔ `63` ↔ `252` |
| normalization | `rank` ↔ `zscore` ↔ `quantile` |
| neutralization | `subindustry` ↔ `industry` ↔ `market` |
| composite | 与另一个高分表达式做加权组合 |

每次变异写一条 `mutations` 记录（`parent_expression_id` / `child_expression_id` / `mutation_axis`），这样才能在后期回答"哪个变异轴最容易把一个 near-pass 变成 pass"这种问题，反哺 Evolution Engine。

### 5.7 Diversity Gate

- 在**提交 simulate 之前**（不是之后）执行。
- 对新表达式计算 embedding，与 `expressions` 表里"历史高分/已提交"子集做 cosine 相似度检查。
- 保留现有 `HistorySimilarityPools` 的 token Jaccard 作为**第一道快速过滤**（便宜、无需模型调用），embedding 相似度作为**第二道精细过滤**（更准但更贵，只对通过第一道的候选跑）——两层过滤兼顾成本和精度，不是简单替换。

### 5.8 L6 — Repair Engine

把现有 `_classify_feedback_reason` 的关键字匹配升级为结构化分类表：

| failure_category | 典型平台反馈 | repair_strategy |
|---|---|---|
| LOW_SHARPE | Sharpe 低于门槛 | 增加 cross-sectional 归一化 / 换更强区分度字段 |
| LOW_FITNESS | Fitness 低 | 检查 turnover 与 sharpe 的比例，调整 decay |
| HIGH_TURNOVER | 换手率过高 | 加长 window / 加 decay |
| CONCENTRATED_WEIGHT | 权重集中 | 加 group_neutralize，检查是否需要 winsorize |
| SELF_CORRELATION | 与历史 alpha 相关性过高 | 触发 L5 变异，优先换 hypothesis 而非只换参数 |
| SPARSE_SIGNAL | 信号过于稀疏 | 复用 skill 文档里 "Rule 5 — Breadth beats sparsity" |
| INCOMPATIBLE_UNIT | 单位不兼容报错 | 直接回退给 L4，标记该字段组合为黑名单 |

每一类失败都写入 `repairs` 表并记录 `success`（修复后是否真的通过），这样 Repair Engine 本身的策略选择也能被 Evolution Engine 评估和调整——"哪种修复策略对哪种失败类型最有效"也是可学习的。

### 5.9 Submission Judge（提交前评分层，v1.1 新增）

这一层填补了一个真实存在的空缺：按你给的数字，约 2 万条生成 alpha 里，能过平台门槛的约 1000+，但去除自相关后能实际提交的只有 300–400 个——**"通过门槛"和"值得提交"之间存在一个几百个候选里选一个子集的决策问题，v1.0 里没有单独设计这一步**，Diversity Gate（5.7）解决的是"要不要花 simulate 配额去跑"，Submission Judge 解决的是"已经跑出来通过门槛了，但提交名额/相关性预算有限，该提交哪些"，两者不是一回事,需要分开。

- 输入：所有 `metric_gate_pass = True` 且尚未提交的候选。
- 打分维度（不是单一 Sharpe 排序）：
  - `novelty_score`：与已提交 alpha 集合的 embedding 相似度（复用 5.7 的 Diversity Gate 逻辑，阈值调低，做排序而不是二元拒绝）；
  - `data_category_coverage`：该候选所属 `research_topics.data_category` 在近期已提交集合里的占比,占比低的加分,鼓励覆盖面；
  - `operator_diversity`：与近期已提交 alpha 的算子结构重合度；
  - `region_universe_coverage`：如果你在多个 Region/Universe 下都有额度,同一 Region 已提交过多时降低该 Region 新候选的优先级；
  - 平台原生指标（sharpe/fitness/turnover）作为基础门槛,而非唯一排序依据。
- 输出：一个综合 `submission_priority_score`，写回 `expressions` 表新增字段，提交队列按此排序,而不是按 sharpe 从高到低硬排。
- 实现建议：先用简单的加权线性组合（每个维度打分后加权求和,权重人工设定并可调）,不需要一开始就上机器学习模型——先用规则跑几周攒够数据,再考虑要不要升级成学习到的排序模型。

### 5.10 Evolution Engine（周期性任务，建议每 500–1000 次 simulate 跑一次）

- 重新计算 `topic_stats`（按 topic 聚合 pass_rate / avg_sharpe / avg_self_corr）。
- 更新 `topic_stats.sampling_weight`：建议用简单的 Thompson Sampling 或 UCB，而不是纯 argmax（避免过早收敛到局部最优、丧失探索）。
- 同步更新现有 `PipelineConfig.max_family_share` 之类的静态阈值——**把这些从硬编码常量改成从 `topic_stats` 动态读取**，这是把"手工调参"升级为"数据驱动"的关键一步。
- 同步更新 Submission Judge（5.9）里各打分维度的权重——例如如果发现 `novelty_score` 高的候选提交后长期表现明显更好，可以逐步调高它在综合分里的权重。

### 5.11 Income Optimizer（后置阶段，v1.1 新增，列入 Phase 7）

方向认可,但明确列为**后置、可选、需要先确认数据可得性**的阶段,不放进 Phase 0–6 的主线,原因见 0.1 节第 3 条。落地前提条件（缺任何一条都不建议开始做）：

1. 你能拿到某种形式的**真实经济回报信号**——不管是 WorldQuant 顾问薪酬结算里能查到的、按 alpha 维度拆分的收入/质量乘数,还是别的可验证代理指标,而不是简单把 Sharpe 当成"收入"的替身重新拟合一遍。
2. 已提交样本量做到有意义的规模（建议至少几百到一千条,且跨越足够长的时间让"表现是否稳定"能被观察到,而不是刚提交就用当天数据训练）。
3. 明确这是一个小样本、高噪音的监督学习问题——建议先用带正则的线性/树模型（Ridge、浅层 GBDT）而不是直接上复杂模型,并且要做时间序列意义上的 train/test 切分（不能随机切分,否则会有未来信息泄漏）,同时把预测区间/不确定性一起输出,而不是只给一个点估计当排序依据。

达成前提后：训练目标 = 已提交 alpha 的真实收入信号；特征 = `operator / field / neutralization / decay / region / delay / universe / self_corr / sharpe / fitness / novelty_score`（后两个来自 5.7/5.9 已经算好的字段，直接复用不用重算）；输出接入 5.9 Submission Judge 作为新增的一个打分维度，而不是替换掉已有维度。

---

## 6. 模块落地映射表

不新建根目录 v51 文件。全部落在 `alpha_mining/` 包内，按现有骨架填充：

| 目标层 | 文件路径（新建/改造） | 状态 |
|---|---|---|
| Knowledge Layer | `alpha_mining/knowledge/ontology.py`, `alpha_mining/knowledge/seed_topics.yaml` | 新建 |
| Research Memory schema | `alpha_mining/storage/sqlite_store.py`（扩展现有类） | 改造 |
| 历史数据回填脚本 | `alpha_mining/storage/backfill_from_csv.py` | 新建 |
| L1 Idea Generator | `alpha_mining/generator/idea.py` | 新建（现为空文件） |
| L2 Hypothesis Generator | `alpha_mining/generator/hypothesis.py` | 新建 |
| L3 Data Mapping | `alpha_mining/generator/data_mapping.py` | 新建 |
| L4 Expression Generator | `alpha_mining/generator/expression.py`（内部 import 并封装现有 `ExpressionFactory`） | 新建，包裹旧类 |
| L5 Mutation Engine | `alpha_mining/mutate/tree_mutation.py` | 新建（现为空文件） |
| Diversity Gate | `alpha_mining/filter/diversity_gate.py`（封装现有 `HistorySimilarityPools`/`NoveltyIndex`） | 新建，包裹旧类 |
| L6 Repair Engine | `alpha_mining/filter/repair.py`（升级现有 `_classify_feedback_reason` 逻辑） | 新建，迁移旧逻辑 |
| Submission Judge（v1.1 新增） | `alpha_mining/filter/submission_judge.py` | 新建 |
| Evolution Engine | `alpha_mining/scheduler/evolution.py` | 新建 |
| Paper Reader（v1.1 新增） | `alpha_mining/knowledge/paper_reader.py` | 新建，Phase 2 后期可选 |
| Income Optimizer（v1.1 新增） | `alpha_mining/scheduler/income_optimizer.py` | 新建，Phase 7，需先满足 5.11 前提条件 |
| Simulation/Submission | `alpha_mining/simulate/*`, `alpha_mining/submitter/__init__.py` | **原样复用，仅在 payload 组装处对接新 schema** |
| 编排入口 | `alpha_mining/main.py`（改造，不再 import 不存在的 v34） | 改造 |

---

## 7. 安全与仓库卫生（必须最先做，独立于架构工作）

1. **立即轮换并撤销** `modelswitch.py` 里暴露的 API Key（`sk-69e64ade...`），不要只是删除代码里的明文——因为它已经进了 git 历史，需要用 `git filter-repo` 或 BFG 清理历史提交，或者干脆考虑仓库设为 private（既然是公开仓库且已经有真实凭证泄露，这是最快止损方式）。
2. 检查 `.env.example` 是否被误改成了 `.env` 并提交过；检查 `fix-codex-proxy.ps1` / `setup-chatgpt-network.ps1` / `chatgpt-proxy.pac` 里是否也硬编码了凭证或内网信息。
3. 把 `总alpha.csv`（69MB）、`pipeline_loop.log`（4.6MB）、`alpha_mining_smoke.sqlite3`、`hopeful_alphas.jsonl.bak_*` 这类运行产物加入 `.gitignore`，从 git 里移除（保留本地文件，只是不再纳入版本控制）。
4. 归档（不删除，移到 `archive/` 或单独分支）v39–v49 的历史主文件，只保留 v50 作为"旧系统兼容层"继续跑，直到新架构验证完成后再退役。
5. 清理 `skills/` 目录，只保留与本项目真正相关的 skill（如有 `pdf-reading` 用于读论文），移除 `react-best-practices`、`pptx`、`pm-*` 系列等无关内容，减少 Claude Code 每次任务前的检索噪音。

---

## 8. 分阶段实施路线图

> 每个阶段结束都要跑一次现有的 `_run_offline_smoke()`（v50 文件里已有的离线冒烟测试）加上新增的针对性测试，确认没有破坏现有 simulate/submit 链路，再进入下一阶段。

### Phase 0：安全与卫生（0.5–1 天）
- 目标：消除凭证泄露风险，清理仓库体积。
- 验收：git 历史不再含明文 key；仓库体积显著下降；`.gitignore` 生效。
- 不涉及任何 alpha 生成逻辑改动，风险为零，应第一个执行。

### Phase 0.5：登录/会话机制加固（紧急，独立于第 2–11 章的架构工作，今天就要做）
- 目标：消除"exceeded number of sign-ins allowed today"复发的可能，见新增第 12 章的完整设计。
- 验收：全部用 mock/本地假 `/authentication` 端点测试，**不允许在验证阶段真实调用 WQ Brain 的登录接口**（你已经被限流，测试阶段还去打真实接口只会让情况更糟）；真实环境的最终验证放到 UTC 每日重置之后，先跑一次最小化的单次登录做冒烟测试，确认没有立刻触发限流再逐步恢复正常调度。

### Phase 1：Research Memory 落地 + 历史数据回填（2–3 天）
- 目标：第 4 章 schema 建好；把 `总alpha.csv` / `通过门槛的alpha.csv` 回填进新表；`topic_stats` 有初始数据（即使 topic/hypothesis 字段暂时是 NULL 或映射为旧 family）。
- 验收：能查询"每个 family 的历史 pass_rate"，且与手工核对 CSV 的结果一致。

### Phase 2：Knowledge Layer + L1/L2/L3（3–5 天）
- 目标：15–25 个种子 topic 入库；Hypothesis Generator 能产出结构化假设并通过语义去重；Data Mapping 能产出带 rationale 的字段候选。
- 验收：人工抽查 20 条 LLM 生成的假设，主观评估"是否像一个合理的量化研究假设"（不是让 LLM 自评）。
- 此阶段**不接入 simulate**，纯离线验证生成质量，成本极低。

### Phase 3：L4 llm_grammar 策略接入 + Diversity Gate 前移（3–5 天）
- 目标：新增的 LLM 语法引导生成策略产出表达式，通过受限文法校验和 Diversity Gate，再进入现有 simulate 引擎。
- 验收：小批量（建议先 50–100 条）跑通全链路，对比 `llm_grammar` vs `template_arch_*` 两种策略的**平台真实反馈**（sharpe/fitness/self_correlation 分布），用真实数据判断新策略是否优于旧模板，而不是主观判断。

### Phase 4：L5 Mutation Engine + L6 Repair Engine 升级（3–4 天）
- 目标：树状变异接入 `HopefulQueue`；失败分类表落地，`repairs` 表开始积累数据。
- 验收：对比"结构化修复"vs"当前 `NearPassAmplifier` 逻辑"在 near-pass 转化率上的差异。

### Phase 5：Submission Judge + Evolution Engine 上线（3–4 天）
- 目标：`submission_priority_score` 开始影响提交队列排序（不再是单纯按 sharpe 排序）；`topic_stats.sampling_weight` 开始动态影响 L1 采样；`PipelineConfig` 里对应的静态阈值改为动态读取。
- 验收：连续跑 1–2 周，观察 topic 分布是否随成功率自动收敛到少数高产 topic，同时保留探索比例；对比引入 Submission Judge 前后，同样 300–400 个提交名额下的 `data_category`/算子结构覆盖面是否更分散。

### Phase 6：旧模板退役评估（数据驱动，不设固定时间）
- 目标：当 `llm_grammar` 等新策略的历史统计（pass_rate、平均 self_correlation、平均 sharpe）持续优于 `template_arch_*` 时，逐步下调旧模板的采样权重，而不是直接删除代码——保留作为兜底和对照组。

### Phase 7：Paper Reader 转正 + Income Optimizer（可选，前提条件见 5.1.1 / 5.11）
- 目标：`hypotheses_staging` 表运转稳定后，评估是否需要把 Paper Reader 从"每周人工触发"升级为定时任务；确认能拿到真实收入信号后，再启动 Income Optimizer 的建模工作。
- 验收：Income Optimizer 的预测排序需要先在历史已提交数据上做回测（用早期数据训练、晚期数据验证），确认比单纯用 Sharpe/Fitness/Novelty 排序更接近真实收入结果，才允许接入 Submission Judge，否则继续用 Phase 5 的规则加权方案。

---

## 9. 成功指标（KPI）

建议在 `topic_stats` 和 `expressions` 表基础上做一个简单的周报（可以是一个脚本生成 markdown/csv，不必做仪表盘），跟踪：

1. **假设多样性**：每周新增的、语义去重后仍然存活的 hypothesis 数量（对照现在的"13 个模板"上限）。
2. **主题级通过率**：`topic_stats.pass_rate`，观察是否有 topic 明显跑赢其他 topic（对应你笔记里 "Capital Efficiency 67% vs Inventory 12%" 的例子）。
3. **提交前相关性拦截率**：Diversity Gate 在 simulate 之前拦掉的候选比例，以及被拦候选如果放行大概率会撞 SELF_CORRELATION 的历史验证准确率。
4. **near-pass 转化率**：`repairs` 表里 `success=1` 的比例，按 `repair_strategy` 分组。
5. **模板 vs LLM 策略对照**：`generation_strategy` 分组下的 pass_rate / avg_sharpe / avg_fitness，作为 Phase 6 退役决策的依据。

---

## 10. 风险与边界提醒（简短，非投资建议）

- 本方案只涉及研究流程与软件架构，不构成、也不应被当作是否应该提交某个具体 alpha、或如何配置真实资金敞口的投资建议——这些判断仍然需要你根据平台规则和自己的研究结论来做。
- WorldQuant BRAIN 平台对 simulate/submit 的频率、相关性审查规则可能会调整，建议 Phase 3 开始前先确认当前的平台限速和自我相关性规则没有变化，现有 `PipelineConfig` 里的阈值（如 `min_sharpe_threshold=1.25`）需要按平台最新要求核对，不要直接假定历史值仍然适用。
- LLM 生成的 hypothesis / data mapping 存在幻觉风险，第 5.4 节强调的"字段候选池必须先过质量过滤器，LLM 不能自由生成字段名"这一约束是硬性的，Claude Code 实现时不能为了"方便"绕过。

---

## 11. 附录：可直接交给 Claude Code / Codex 的执行 Prompt

> 使用方法：把下面这个模块的 Prompt **单独发给** Claude Code / Codex，一次只做一个 Phase，做完验收再进入下一个。不要把 Phase 0–6 一次性拼成一个巨型 Prompt 丢过去。

### Phase 0 执行 Prompt（复制这一段）

```
你正在处理仓库 Metroids048/alpha（分支 codex/v50.4-pipeline-recovery）。
在做任何功能性改动之前，请先执行以下安全与卫生任务，这是最高优先级：

1. 检查 modelswitch.py 中硬编码的 OPENAI_API_KEY，将其从代码中移除，改为从环境变量
   OPENAI_API_KEY 读取（读取失败时给出清晰报错，不允许静默使用空字符串）。
2. 全仓库搜索是否还有其他硬编码的 API Key / Token / 密码（重点检查 .ps1、.pac、
   .env、.json、.py 文件），列出清单，不要自作主张删除内容，先向我报告。
3. 生成一份 .gitignore（如已存在则更新），至少排除：*.log、*.sqlite3、
   总alpha.csv、通过门槛的alpha.csv、hopeful_alphas.jsonl.bak_*、
   pipeline_loop_state.json、pipeline_supervisor_state.json、work/ 目录。
   不要删除这些文件本身，只是让它们后续不再被 git 追踪。
4. 把根目录里 auto_alpha_pipeline_rebuilt_v39.py 到 v49.py（除 v50 外的所有历史版本）
   移动到 archive/legacy_pipeline_versions/ 目录下，保留文件内容不变，只是移动位置，
   并在该目录下新建一个 README.md 简单说明这些是历史版本，当前生产版本是 v50。
5. 清理 skills/ 目录，移除以下与本项目无关的子目录：react-best-practices、pptx、
   ui-ux-pro-max-skill-main、以及所有 pm-* 前缀的目录、skills-main、superpowers-main、
   space-* 前缀目录、mcp-builder、skill-creator、webapp-testing、
   planning-with-files-master。保留（如果与阅读 PDF/文档相关的 skill 存在则保留）。
   执行前先列出将被删除的目录清单给我确认。

完成后给我一份变更摘要，不要提交（commit）任何改动，先让我 review diff。
```

### Phase 1 执行 Prompt（复制这一段，在 Phase 0 验收通过后使用）

```
你正在处理仓库 Metroids048/alpha。请阅读项目根目录下的
AI_Quant_Researcher_重构方案_v1.md 第 4 章（Research Memory 数据库 Schema）
和第 6 章（模块落地映射表），然后执行：

1. 在 alpha_mining/storage/sqlite_store.py 中，保留现有 SqliteRunLog 类和
   simulation_runs 表不变，新增第 4 章列出的全部表（research_topics、
   hypotheses、data_mappings、expressions、mutations、repairs、topic_stats），
   以及对 simulation_runs 的 ALTER TABLE 扩展字段。所有新表创建都要用
   CREATE TABLE IF NOT EXISTS，保证幂等、可重复执行。

2. 新建 alpha_mining/storage/backfill_from_csv.py，实现一个脚本：
   - 读取根目录的 总alpha.csv 和 通过门槛的alpha.csv
   - 对每一行，在 expressions 表中插入一条记录（expression_text 取自 expression
     列，generation_strategy 取自 family 列，如果 family 为空则记为
     "legacy_unknown"，normalized_text 和 structure_sig 复用
     auto_alpha_pipeline_rebuilt_v50.py 中已有的 _normalized_expression() 和
     _structure_signature() 函数逻辑，不要重新实现，import 复用）
   - 对每一行同时在 simulation_runs 表中插入对应的模拟结果记录，字段按列名
     直接映射（sharpe/fitness/turnover/returns/drawdown 等）
   - 脚本要支持重复运行不产生重复数据（用 expression 的 normalized_text 做唯一性判断）
   - 跑完后打印一份统计摘要：总导入行数、按 generation_strategy 分组的行数、
     按 generation_strategy 分组的平均 sharpe/fitness

3. 跑一次这个回填脚本，把结果统计摘要贴给我看，先不要做任何进一步的架构改动。
```

（Phase 2–6 的执行 Prompt 请在完成前序阶段验收后，参照本文档第 5、6、8 章的对应小节，用同样的格式——"读取文档对应章节 + 指定具体文件路径 + 明确验收动作"——现写现用；不建议提前把后续阶段的 Prompt 也写死，因为 Phase 1 的回填结果会影响 Phase 2 里种子 topic 清单的具体设计。）

---

## 12. 登录/会话加固方案（Phase 0.5，紧急）

### 12.1 修复范围界定

这是**唯一被允许触碰"已打通平台仿真/提交"这部分代码的例外**。第 7 章"安全与仓库卫生"里已经说过 `alpha_mining/simulate/async_batch.py`、`resilient_async.py` 和 `auto_alpha_pipeline_rebuilt_v50.py` 里 simulate/submit/poll 的 HTTP 调用逻辑本身不允许动——**这个界定不变**。但登录/会话管理是这些文件里一个相对独立的子问题，且是当前真正线上出故障的部分，所以单独开一个 Phase 0.5，允许对**认证相关的代码**做改动，范围严格限定为：

**允许改的**：
- `auto_alpha_pipeline_rebuilt_v50.py` 里的 `authenticate()` 函数本身，以及全部 10 处 `self.authenticate()` 调用点（只做调用替换，不改调用点周围的业务逻辑）。
- `alpha_mining/simulate/async_batch.py` 里的 `_authenticate()` 函数。
- `run_pipeline_supervisor.py` 里子进程启动前的会话恢复逻辑。
- 新增文件（不涉及修改现有 simulate/submit 逻辑）。

**不允许改的**：
- `_retry()`、`_submit_one()`、`_sim_payload()`、进度轮询、429/熔断/死信队列相关的任何逻辑——这些和登录无关，按第 7 章的规定原样保留。

### 12.2 设计方案

新增 `alpha_mining/auth/session_manager.py`，作为**全项目唯一的登录入口**，取代分散在两处的 `authenticate()` / `_authenticate()`：

1. **会话复用 + 冷却期**：暴露 `ensure_authenticated()` 而不是 `authenticate()`。内部维护 `last_auth_ts`，只有满足以下任一条件才真正发起登录请求：会话从未建立过；距上次成功登录已超过冷却期（建议默认 25 分钟，具体以 WQ Brain session 实际有效期为准，需要 Claude Code 先去平台文档 / 现有代码里找有没有 session 有效期的线索，找不到就保守设置）；或者刚收到一次明确的 401（说明服务端已经判定会话失效）。其余情况一律直接复用已有 session，不发请求。
2. **跨进程持久化**：把最近一次登录成功的时间戳（不需要、也不建议保存明文密码或 token 到磁盘，除非平台的认证方式本身就要求保存 cookie/token，那也要确保这个文件被加进 `.gitignore`）写到本地状态文件，例如 `.wq_auth_state.json`。`run_pipeline_supervisor.py` 重启子进程时，新进程读取这个状态文件，如果距上次登录还在冷却期内，就不重新登录、等 429/401 真实发生了再说——这一条直接解决"崩溃重启=重新登录"的问题。
3. **跨路径加锁**：同步（`requests`）和异步（`aiohttp`）两条路径必须通过同一个 `session_manager` 判断是否需要登录，用文件锁或进程内锁保证同一时刻只有一次真实的 `/authentication` 请求在飞行中，另一路径等待并复用结果，而不是各发各的。
4. **每日硬顶（safety cap）**：`session_manager` 自己维护一个"今天（UTC）已经发起过几次真实登录"的计数器，持久化到同一个状态文件。设一个远低于平台真实限制的安全阈值（比如 5 次/天，具体数字可以后续根据平台真实限制调整，但初期宁可保守）。一旦达到阈值，**拒绝再发起新的登录请求，直接抛出清晰的错误信息**（例如"今日登录次数已达安全上限，请检查是否存在异常重试，如确认需要请手动重置计数器"），而不是像现在这样继续重试到底，把限流变得更严重。
5. **把"重建 HTTP 会话"和"重新登录"这两件事拆开**：网络抖动/SSL 错误只应该触发"重建本地连接对象"（不消耗平台登录配额），只有在重建后的会话真的收到 401 时，才允许升级为一次登录请求。当前代码把这两件事耦合在一起，是每次网络抖动都可能变成一次额外登录的根本原因之一。

### 12.3 验收标准

- **单元测试必须用 mock 服务器**，模拟 `/authentication` 端点，断言：短时间内（比如 1 分钟内）连续触发 20 次原本会调用 `authenticate()` 的路径（同步 + 异步混合），mock 服务器实际收到的登录请求数 = 1。
- **模拟崩溃重启场景**：连续启动/杀死子进程 5 次（用 mock 服务器），断言只有第一次真正登录，后续 4 次都复用了持久化的状态、没有发起新登录。
- **模拟达到每日上限场景**：把安全阈值临时调到 2，连续触发 3 次登录，断言第 3 次被拒绝并给出清晰报错，而不是继续重试。
- **真实环境验证放到最后，且要谨慎**：以上全部用 mock 跑通之后，才允许在 UTC 每日重置后，用真实凭证做**一次**登录做冒烟测试；如果当天仍处于被限流状态，先不做真实验证，改天再验证，不要因为想验证修复效果而再次触碰真实登录接口。

---

## 13. Master 编排 Prompt（一次性交给 Claude Code / Codex）

### 13.1 关于"自动逐模块推进"的设计取舍

你要求"一个模块完成后测试和审查，通过后再继续下一个模块直到完成"。这里有一个需要你知道的取舍：对于**新增、不触碰现有代码的模块**（比如 Research Memory 新表、L1–L3 生成器、Knowledge Layer），可以让 Claude Code 自动测试、自动判断通过与否、自动继续，风险很低，因为出错也只是新代码有 bug，不影响现在能跑的东西。

但对于**会touch到共享入口或现有文件的步骤**（比如把 `main.py` 接到新模块、把 `authenticate()` 调用点替换掉、把旧模板和新生成策略接进同一个候选池），我在下面的 Prompt 里加了一道"硬性暂停点"：这类步骤测试通过后，Claude Code 仍然要停下来，把改动摘要写出来，等你显式回复"继续"才能往下走——不是它自己判断"看起来没问题就继续"。原因很直接：这是一个每天在跑真实资金相关产出的系统，全自动无人值守地改动共享入口，一旦判断错误，代价比多花你几分钟看一眼 diff 高得多。这个设计我建议保留，而不是为了"全自动"去掉这道保险。

### 13.2 Prompt 正文（整段复制给 Claude Code / Codex）

```
你正在负责重构仓库 Metroids048/alpha（分支 codex/v50.4-pipeline-recovery）。

======================================================================
第一部分：信息来源与执行原则（每次开始新的一天/新的会话都要重新确认）
======================================================================

1. 唯一的架构基准文档是仓库根目录的 AI_Quant_Researcher_重构方案_v1.md（当前版本
   v1.2）。开始任何工作之前，先完整读一遍这份文档的第 1、2、3、6、8、12 章。
2. 按 AGENTS.md 的规定，先扫描 skills/**/SKILL.md，看是否有和当前任务相关的技能
   文档需要读。
3. 你要做的不是一次性重写，而是严格按文档第 8 章的阶段顺序，一次只做一个 Phase
   里的一个模块。当前从 Phase 0.5 开始（登录加固，最高优先级，今天就要处理），
   然后 Phase 0（安全与仓库卫生），再按 Phase 1 -> 2 -> 3 -> 4 -> 5 -> 6 顺序推进
   （Phase 7 需要满足文档 5.11 的前提条件，不要主动开始）。

======================================================================
第二部分：绝对不允许触碰的文件与逻辑（硬性红线）
======================================================================

除非任务明确是 Phase 0.5（登录加固）里列出的例外，否则以下内容不允许修改，
包括不允许"顺手优化""顺手重构"：

- alpha_mining/simulate/async_batch.py 和 resilient_async.py 里除 _authenticate()
  以外的所有逻辑（并发调度、429 处理、熔断器、死信队列、_submit_one、_sim_payload）
- auto_alpha_pipeline_rebuilt_v50.py 里所有 simulate / submit / poll 相关的 HTTP
  请求构造、重试逻辑本身（authenticate() 函数和其调用点除外，那属于 Phase 0.5）
- 任何已经在生产环境跑着的、能正常提交 alpha 的路径，除非该 Phase 的任务书明确
  要求修改它

如果你发现某个任务看起来需要修改这些文件里的"红线"部分才能完成，停下来，向我
报告冲突，不要自己决定绕过限制。

======================================================================
第三部分：Phase 0.5 —— 登录加固（今天优先做，见文档第 12 章）
======================================================================

背景：今天已经触发平台的 "You may have exceeded the number of sign-ins allowed
today" 限流，这是生产事故，优先级高于其他所有 Phase。

任务：
1. 按文档 12.2 节的设计，新建 alpha_mining/auth/session_manager.py。
2. 把 auto_alpha_pipeline_rebuilt_v50.py 里全部 10 处 self.authenticate() 调用点，
   以及 alpha_mining/simulate/async_batch.py 里的 _authenticate()，改为通过
   session_manager.ensure_authenticated() 判断是否需要真正登录。
3. run_pipeline_supervisor.py 的子进程重启逻辑，接入 session_manager 的持久化状态。
4. 按文档 12.3 节写单元测试，全部用 mock /authentication 端点，不允许测试代码里
   出现真实的 api.worldquantbrain.com 请求。
5. 测试全部通过后，停下来，给我一份变更摘要（改了哪些文件、新增了哪些文件、
   加了哪些配置项，比如冷却期时长、每日安全上限具体设成了多少），等我确认后再继续。
   不要自己决定去做真实环境的登录验证——那一步只有我确认"已过 UTC 每日重置"之后
   才能做，而且只能做一次。

======================================================================
第四部分：Phase 0 —— 安全与仓库卫生
======================================================================

按文档第 7 章执行（撤销/轮换 modelswitch.py 里的明文 API Key、清理 .gitignore、
归档旧版本文件、清理无关 skills 目录）。这个 Phase 全部是删除/移动/新增
.gitignore 规则，不改动任何业务逻辑，测试通过标准是：现有的离线冒烟测试
（v50 文件里的 _run_offline_smoke，如果存在的话；不存在就先找到项目里等价的
最小验证方式）在改动前后行为一致。完成后同样给我一份摘要，等确认后继续。

======================================================================
第五部分：Phase 1 及之后 —— 按"单模块"粒度推进
======================================================================

从 Phase 1 开始，把文档第 6 章"模块落地映射表"里的每一行当成一个独立模块，
每次只做一个模块，按以下循环执行：

  1. 【读】重新读一遍文档里这个模块对应的设计小节（第 4/5 章里的具体条目）。
  2. 【写】实现这个模块，只新增/修改该模块映射表里指定的文件路径，不顺带碰
     其他文件。
  3. 【测】为这个模块写针对性的单元测试/集成测试（新模块必须自带测试，不能
     只靠人工看代码判断"应该没问题"）。凡是涉及调用真实 WorldQuant BRAIN
     接口的地方，一律用 mock，不允许消耗真实的 simulate/登录配额来做常规测试。
  4. 【审】测试全部通过后，自己先做一次简短的代码审查，检查：
     a) 是否越界修改了第二部分列出的红线文件；
     b) 是否真的没有改动任何现有可以正常工作的路径；
     c) 新增的数据库表结构是否和文档第 4 章的 Schema 一致。
     把这次审查的结论（通过 / 发现了什么问题 / 已自行修复了什么）写成简短摘要。
  5. 【决定是否暂停】：
     - 如果这个模块是纯新增文件、完全没有修改任何已有文件 —— 测试通过、
       审查无问题后，直接继续做该 Phase 里的下一个模块，不需要等我确认，
       但仍然要把摘要记录下来，方便我随时抽查。
     - 如果这个模块涉及修改任何已有文件（哪怕只是把 main.py 接上新模块的
       一行 import）—— 测试通过后必须停下来，把摘要和关键 diff 展示给我，
       等我明确回复"继续"之后才能做下一个模块。
  6. 每完成一个 Phase（不是每个模块，是整个 Phase），无论前面是否有过自动
     连续推进的模块，都必须停下来做一次 Phase 级别的总结汇报，等我确认后
     再开始下一个 Phase。

======================================================================
第六部分：出问题时怎么办
======================================================================

- 如果某个模块的测试怎么都跑不通，不要为了"能过"而放松测试标准或者删掉
  测试断言，停下来向我报告具体卡在哪。
- 如果发现文档里的设计和现有代码实际情况对不上（比如某个我在文档里假设
  存在的函数其实不存在），停下来向我报告这个出入，不要自己瞎猜着改。
- 如果任务需要访问网络但当前网络配置访问不了必要的域名，明确告诉我需要
  开哪个域名，不要静默跳过相关功能。

现在，从第三部分（Phase 0.5 登录加固）开始。
```

---

## 14. 一句话总结

现有系统的工程基础（并发 simulate 引擎、429/熔断/死信队列、初步的去重和失败分类）比表面看起来扎实，值得保留；真正缺的是"研究假设层"和"能积累、能学习的记忆层"，这两块从零补齐即可，不需要推翻重来。用 Strangler Fig 方式把新的六层架构接到现有 `alpha_mining/` 骨架上，用真实的平台反馈数据（而不是主观判断）决定新旧生成策略的采样权重，同时先花半天时间处理泄露的 API Key——这是当前唯一真正紧急的问题。
