# Consultant Alpha Factory vNext 独立代码审计报告

审计日期：2026-07-20  
审计范围：当前本地目录中的 `alpha_mining` vNext、仍可触达的旧流水线入口、测试、历史导入数据与提交路径。  
结论：**代码门禁已达到 fail-closed；当前数据状态尚未达到 shadow-run 条件。**

## 1. 发现并已修复的问题

### 动态门槛与提交安全

- 删除了 vNext 配置中伪装成平台门槛的静态 Sharpe/Fitness/Turnover 等过滤值；配置只保留内部安全边际和预算。
- 提交守卫此前可能接受缺失或未完成的 checks。现要求 checks 非空、所有必需 check 明确为 `PASS`，且 `SELF_CORRELATION` 必须存在并为 `PASS`。`PENDING`、`MISSING`、`UNKNOWN`、`ERROR/API ERROR`、空结果和未完成状态均阻止提交。
- 队列入队与真正执行时均重新核对 Dynamic Gate Registry 的 snapshot version、门槛覆盖范围和新鲜度；过期、缺失或版本变化均阻止提交。
- 不再信任调用者提供的布尔质量结论；入队和执行阶段均使用候选原始指标与动态 gate limit 重新计算内部安全边际。
- `execute_ready()` 默认 `execute=False`；CLI 只有配置显式开启并提供确认短语后才传入 `execute=True`。
- 同一 `alpha_id` 已在 `PROCESSING` 或 `SUBMITTED` 时禁止重复提交。
- 历史 monolith、生成归档和辅助提交脚本的真实提交入口已隔离/禁用；legacy `--execute-submit` 入口直接返回 BLOCKED。
- Gate Registry 重复观测不再错误推进版本；在线 gate 同步使用实际抓取时间，而不是旧 Alpha 创建时间。

### Correlation

- 本地相关性以 `max(abs(Pearson), abs(Spearman))` 判断，负号翻转仍视为高相关。
- 强制最小日期 overlap；无足够重叠、零方差或无法计算系数时返回 `INSUFFICIENT_HISTORY`，不再当作低相关 PASS。
- behavior signature 折叠 sign/scalar/offset/window-neighbor/settings-only 变化，防止把系数、偏移、相邻窗口或设置微调冒充新 behavior family。
- 平台 `FAIL` 永远不能被本地判断覆盖；平台 `PENDING` 也不能被本地 `PASS` 覆盖，因为提交守卫要求平台相关性 check 明确 PASS。

### Legacy Import / Triage

- 导入器采用 chunk streaming，并安全提高 CSV field-size limit；已用本地 68,951,109-byte `总alpha.csv` 完整验证。
- canonical expression 按规范化哈希去重，同时保留原 `alpha_id` 和 lineage。
- medoid 选择具有稳定排序与确定性；每个 cluster 最多一个 medoid 可进入 `RECHECK`，其余成员不会全部重模拟。
- `ARCHIVE` 不会生成 settings trial，也不会消耗模拟预算。
- 修复了真实历史 triage 中暴露的 SQLite `database is locked`：gate 读取移至写锁之前，并按 cluster 提交事务。
- Legacy importer 增强递归敏感字段与邮箱清洗。

### Settings Optimizer

- 搜索为 OFAT 局部邻域，不生成完整笛卡尔积。
- 同时实施每 candidate 严格预算和全局预算；仅 near-pass 或高潜力候选允许搜索。
- settings 不进入 behavior family identity。
- Decay/Truncation trial 明确标记为 `STABILITY_TURNOVER_ONLY`，不可用于声称规避相关性。

### API、幂等与测试隔离

- 同步客户端具有最小请求间隔、有限重试、`Retry-After`（秒数及 HTTP-date）解析，401 最多重新认证一次。
- simulation 提交与 polling 同样限制 401 重认证次数；polling 有总超时。
- 新增持久化 `simulation_requests` 幂等表，以 type/settings/expression 的精确 payload hash 原子 claim；同一 simulation 不会重复 POST。
- dry-run 不调用写 endpoint。
- pytest 自动安装 socket guard，禁止测试解析或访问 `*.worldquantbrain.com`；本地 mock/localhost 测试仍可运行。

### 依赖方向和类型问题

- `alpha_mining` 内已无 `auto_alpha_pipeline_rebuilt_v50` import；核心表达式 AST、字段目录、相关性、策略、legacy、提交守卫均位于 vNext 领域/服务模块。
- 修复了类型检查揭示的字段目录构造参数歧义、nullable payload、异步 task set、提交 client protocol、LLM protocol 返回类型等问题。

## 2. 安全审计

- 在 `Alpha.ipynb` 发现真实 Gmail 地址和密码字面量，已替换为占位符。
- 当前扫描未发现真实邮箱、密码、Authorization token 或明文 cookie 被写入生成报告/日志。
- `.gitignore` 覆盖 `.env`、`.wq_auth_state*`、`.wq_browser_cookie.json`、SQLite 数据库和审计报告。
- 本地 `.wq_browser_cookie.json` 被保留为用户本机认证状态；代码会迁移到 Windows DPAPI 保护的状态，不应提交。
- 当前目录**不是 Git repository**，因此只能确认当前文件内容和 ignore 规则，无法从本地证据证明敏感信息“历史上从未进入 Git”。

## 3. 回归测试与静态检查

- `python -m pytest -q`：**467 passed, 5 subtests passed in 59.73s**。
- 新增审计回归覆盖：fail-closed check 状态、snapshot 过期/版本变化、动态质量复算、execute 默认关闭、重复提交、absolute/sign-flip correlation、insufficient history、结构变体、legacy 去重/medoid/预算、429/401/poll timeout、simulation 幂等、dry-run 无写请求、真实 API 网络隔离。
- Ruff：`All checks passed!`
- Mypy（`alpha_mining`，忽略缺失第三方 stubs）：通过，0 errors。
- `compileall`：通过。
- 当前环境未安装 pyright、pylint、flake8；未把“工具不可用”表述为代码通过。

## 4. 当前历史数据只读验证

执行命令：

```text
python -m alpha_mining legacy import
python -m alpha_mining legacy triage
python -m alpha_mining legacy report
python -m alpha_mining submit dry-run
```

结果：

- Import：`scanned=18659 canonical=13325 lineage=18630 chunks=14 checks=129841 gates=129841`。
- canonical hash 重复数：`0`；lineage 中保留非空 `alpha_id` 的记录：`16194`。
- Triage：`clusters=8739 medoids=8739`；`ARCHIVE=288`，`SEED_ONLY=13037`，`RECHECK=0`。
- 同一 cluster 多个 RECHECK：`0`；archived settings trials：`0`。
- Report：inventory `18630`、cluster summary `8739`、seed `13037`、archive `288`、repair `0`。
- Submit dry-run：`endpoint_calls=0 queue={} candidates=0 allowed=0 blocked=0 reasons={}`。

dry-run 的 blocked 数为 0，并不代表有候选通过：当前 triage 没有产生任何 `RECHECK`/submission candidate，因此候选总数就是 0。13,037 条 canonical 记录被归为 `SEED_ONLY`，原因为 `missing_metrics_or_gate`；另 288 条因表达式无效归档。

## 5. 未修复问题及原因

- Dynamic Gate Registry 在最近 24 小时内的新鲜 snapshot 数为 `0`；现有 snapshot 只有历史的 `CONCENTRATED_WEIGHT`、`HIGH_TURNOVER`、`LOW_FITNESS`、`LOW_SHARPE`、`LOW_SUB_UNIVERSE_SHARPE`、`LOW_TURNOVER`。
- Registry 中没有 `SELF_CORRELATION` 或 `PROD/PRODUCTION_CORRELATION` snapshot。
- 这些缺口必须通过平台只读 check/gate 同步获得真实、带时间戳的平台证据；本次按要求未调用真实 WorldQuant 写 endpoint，也不能用本地常量伪造平台门槛，因此未“补造”数据。
- 因目录没有 `.git` 元数据，无法完成历史 commit 泄密追溯；如需证明 Git 历史清洁，必须提供实际 repository/history。

## 6. Shadow-run 判定

**未达到 shadow-run 条件。**

阻止原因：

1. 24 小时内 fresh gate snapshots 为 0；
2. 缺失平台 SELF_CORRELATION 与 PRODUCTION_CORRELATION gate evidence；
3. 13,037 条候选缺少足够指标或动态 gate，全部保持 `SEED_ONLY`；
4. 当前没有 RECHECK 候选，dry-run 尚未对真实候选形成完整的端到端 shadow 证据。

进入 shadow-run 前应先执行只读平台 gate/check 同步，并确认上述 correlation gates、核心质量 gates、scope 与 snapshot freshness 均完整；在此之前，现有 fail-closed 行为会继续阻止候选进入提交队列。
