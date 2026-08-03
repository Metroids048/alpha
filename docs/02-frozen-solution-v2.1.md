# 冻结解决方案 v2.1

## 1. 状态

- 基线：`main@2263f69c25d6194378ccafcbcc53c434fe7b36ed`，实施工作区包含用户已有改动。
- 唯一生成入口：`生成Alpha.py -> alpha_mining.factory.runtime.main`。
- 冻结状态：代码实施完成；平台运行验收仍依赖有效认证和完整 catalog。
- 禁止恢复 `QualityAlphaWorkflow`，禁止建立第二条认证、模拟、READY 或提交链路。

## 2. 根因和方案

| 问题 | 根因 | 冻结方案 | 实施状态 |
| --- | --- | --- | --- |
| P-001 / RC-001 | 目录索引、认证和工程说明可被当成 Alpha 灵感正文 | S-001：文档类型、意图和正相关检索门 | 已实施 |
| P-002 / RC-002 | 确定性 fallback 未使用知识却声称引用知识 | S-002：usage mode 与独立 context provenance | 已实施 |
| P-003 / RC-003 | 平台 check 的重复 PASS 可掩盖 FAIL；ProdCorr 例外范围过大 | S-003：统一 fail-closed quality 判定 | 已实施 |
| P-004 / RC-004 | 连续低效研究臂只有统计权重，没有调用预算后果 | S-004：运行时 arm quota gate | 已实施 |
| P-005 / RC-005 | NEAR_PASS 没有阶段、预算、赢家承接或 lineage | S-005：持久化顺序 Tune | 已实施 |

## 3. 实施顺序

`S-003 -> S-001 -> S-002 -> S-004 -> S-005`

质量判定先于调参。Tune child 必须继续经过：

`FactoryOrchestrator.execute_candidate -> 平台请求生命周期 -> evaluate_quality -> outcome/feedback/READY evidence`

## 4. S-003：质量判定

- 相同平台 check 的多条结果使用最坏状态；PASS 不能覆盖 FAIL/ERROR/REJECTED。
- 除明确确认的 `PROD_CORRELATION` 例外外，任意平台 FAIL 都阻止 READY。
- UNKNOWN、MISSING、PENDING 与 WAITING 继续 fail-closed，不映射为 LOW_SHARPE。

## 5. S-001：知识检索

- 文档类型：`IDEA_BODY`、`RULE`、`INDEX`、`AUTH`、`ENGINEERING`、`UNKNOWN`。
- IDEA 检索只接受正文和允许的规则；目录、认证和工程材料被排除。
- 只有正相关片段可进入上下文；无正文为 `INCOMPLETE`，无相关正文为 `NO_RELEVANT_MATCH`。
- 最多 5 段、每段 1200 字符、总计 6000 字符、每源最多两段。
- 结果按分数和 ref 稳定排序，并写入可复现 `context_hash`。

## 6. S-002：知识真实性

- `LIVE_LLM_KNOWLEDGE`：生成 refs 必须来自检索 context。
- `DETERMINISTIC_NO_KNOWLEDGE`：生成 refs 必须为空；仅可保留 context refs，且 `degraded=true`。
- `NONE`：v50 fallback 未声明知识使用。
- provenance 贯穿 simulation request context、`candidate_outcomes` 和 READY CSV。
- 结构化 LLM 仅在 `ALPHA_ENABLE_KNOWLEDGE_LLM=1` 且存在本地配置时调用；否则不外部调用、不伪造知识驱动结果。

## 7. S-004：研究臂预算

- 权重 `0.0` 不准入。
- 存在高于 `0.1` 的臂时，`0.1` 探索臂不准入。
- 所有可用臂都不高于 `0.1` 时，仅保留一个确定性探索位。
- `0.1 < weight < 1.0` 每策略族最多一个；`1.0` 使用常规周期上限。

## 8. S-005：Sequential Tune

- 仅 `NEAR_PASS` 父候选可进入，最多 2 个父候选、每父最多 4 个 child。
- 顺序阶段：`STABILITY -> DECAY_COARSE -> DECAY_FINE`；每个 trial 只变更一个设置。
- 前一阶段更优结果才成为下一阶段 baseline；Decay 先在 2/8 粗搜，再邻域细搜。
- 每个 trial 在模拟前写入 reservation；24 小时滚动额度、request hash、父候选、stage、设置、终态和 outcome 都持久化。
- Tune 可重用同一 expression 的 identity，但必须携带父 lineage，仍经原请求生命周期，不能直接模拟或提交。

## 9. 数据迁移

- v20 为 `candidate_outcomes` 增加 knowledge usage、context refs/hash 和 degraded 字段。
- v21 为 `settings_trials` 增加 Tune lineage、settings、request hash 与终态字段及索引。
- 迁移补齐稀疏旧库缺失的 `settings_trials`，保持幂等；`backup_and_migrate()` 仍执行备份和 `PRAGMA integrity_check`。

## 10. 禁止项

- 不接入 PyQt GUI、BrainClient、认证/提交实现、SubmitWorker、`exec()`、100,000 次重试、表达式占位 DSL 或 alpha系统运行时依赖。
- 不绕过 catalog/auth，不能伪造离线成功或 READY CSV。
- 不进行真实 Brain simulate、submit 或任何网络认证操作。

## 11. 验证证据

| 检查 | 结果 |
| --- | --- |
| 入口 `生成Alpha.py --help` | 通过 |
| targeted runtime/quality/catalog/request/Tune suite | 69 passed |
| 全量 `pytest -q` | 通过，47.8 秒 |
| `compileall` | 通过 |
| `git diff --check` | 通过 |

完整命令输出记录在 `docs/frozen_solution_targeted_tests_fresh.txt`。

## 12. 运行边界

真实启动若仍返回 `CATALOG_UNAVAILABLE`，这是认证状态、会话过期或三类 catalog 缓存缺失的真实状态。必须恢复授权会话并完成只读 catalog sync；不得修改认证保护、缓存时间或质量门槛来制造成功。
