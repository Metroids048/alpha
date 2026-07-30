# Consultant Alpha Factory 第一次 Shadow Run

## 结论

本次运行按 fail-closed 结束于 seed 选择之前，未执行模拟、未生成 offspring、未形成最终候选。真实 submit endpoint 调用数为 **0**。这不是零候选成功样本，而是前置证据不完整导致的受控阻断。

## Gate Snapshot

- 快照文件：`gate_snapshot.json`
- 来源：本地 `总alpha.csv`，不是 live platform refresh
- Snapshot：30；24 小时内新鲜：0
- 最早/最晚 last_seen：2026-05-31T21:19:39Z / 2026-07-03T13:25:52Z
- Gate 分布：{"CONCENTRATED_WEIGHT": 1, "HIGH_TURNOVER": 6, "LOW_FITNESS": 6, "LOW_SHARPE": 6, "LOW_SUB_UNIVERSE_SHARPE": 5, "LOW_TURNOVER": 6}
- 缺失必需 Gate：SELF_CORRELATION, PROD_CORRELATION
- 判定：缺失或过期，提交阶段不可用

## 历史 Alpha 与 triage

| 指标 | 数量 |
|---|---:|
| 源文件扫描行（本轮 import 输出） | 18,659 |
| 历史 Alpha 记录 | 18,630 |
| 唯一表达式 | 13,325 |
| behavior cluster | 8,739 |
| RECHECK | 0 |
| REPAIR | 0 |
| SEED_ONLY | 13,037 |
| ARCHIVE | 288 |

失败分布：`missing_metrics_or_gate=13,037`，`invalid_expression=288`。每个 cluster 的 recheck candidate 实际均为 0，没有超过默认最多 1 个的限制。

## Shadow Run 漏斗

| 阶段 | 数量 |
|---|---:|
| 实际选择 seed | 0 |
| 生成 offspring | 0 |
| 预筛淘汰 | 0 |
| 模拟 | 0 |
| 基础门槛通过 | 0 |
| 顾问质量缓冲通过 | 0 |
| 自相关明确通过 | 0 |
| 自相关 PENDING | 0 |
| 最终 dry-run 候选 | 0 |

0 个 PENDING 不是“相关性已通过”：候选生成前即被阻断，因此没有候选进入相关性检查。`alpha_daily_returns` 的可用 return series 为 0，本地 max correlation 分布不可计算；状态为 `NOT_EVALUATED_NO_CANDIDATES_AND_NO_RETURN_SERIES`。

## 平台相关性结果分布

本轮没有新模拟，因而没有获取新的平台 checks。历史 `alpha_check_events` 中的相关性结果为：SELF_CORRELATION:PENDING=16120。Gate Registry 中没有 `SELF_CORRELATION` 与 `PROD_CORRELATION` snapshot，因此任何 PENDING/MISSING/UNKNOWN 均不能进入最终队列。

## Family 与 settings 效率

本轮所有 family 的模拟次数均为 0，“每 100 次模拟通过率”统一记为 `N/A`，详见 `shadow_run_family_metrics.csv`。本轮没有运行 settings profile；`shadow_run_settings_metrics.csv` 记录 `NOT_RUN`，未使用完整笛卡尔积。

## 最终候选完整证据

最终候选为 0，因此没有可列出的完整通过证据。`shadow_run_candidates.csv` 仅保留严格证据字段表头，没有伪造候选。

## Blocked 原因

1. `GATE_SNAPSHOT_STALE_OR_MISSING`：30 个 snapshot 中 24 小时内新鲜数为 0。
2. `REQUIRED_CORRELATION_GATE_SNAPSHOT_MISSING`：缺失 SELF_CORRELATION, PROD_CORRELATION。
3. `LOCAL_RETURN_SERIES_MISSING`：本地 return series 为 0。
4. `SHADOW_RUN_CLI_CONTRACT_UNSUPPORTED`：当前 CLI 拒绝全部五个强制参数；现有实现只做 generate，不做模拟或报告。

候选级 blocked 集合为空，因为没有候选被生成；上述运行级 blocker 已完整写入 `shadow_run_blocked.csv`。`submit dry-run` 的实际输出为 `endpoint_calls=0, candidates=0, allowed=0, blocked=0`。

## 是否扩大预算

**不建议。** 当前瓶颈不是 64 次模拟预算，而是 Gate 新鲜度、相关性 Gate、daily returns 和 Shadow Run CLI 实现缺失。扩大预算不会产生可信通过样本。应先完成只读 live Gate/check 同步、return ingestion，并实现/验证用户指定的有界 Shadow Run 合同；之后再用现有 64 次上限做小规模运行。
