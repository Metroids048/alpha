"""Generate fail-closed Shadow Run artifacts after a blocked CLI execution.

This collector is intentionally read-only with respect to Research Memory.  It
does not authenticate, simulate, enqueue, or call any submission endpoint.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "research_memory.sqlite"
GATE_SNAPSHOT = ROOT / "gate_snapshot.json"
FRESHNESS_HOURS = 24
REQUIRED_GATES = (
    "LOW_SHARPE",
    "LOW_FITNESS",
    "LOW_TURNOVER",
    "HIGH_TURNOVER",
    "LOW_SUB_UNIVERSE_SHARPE",
    "SELF_CORRELATION",
    "PROD_CORRELATION",
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _query_dict(connection: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(name): int(count) for name, count in connection.execute(sql)}


def main() -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    snapshots = json.loads(GATE_SNAPSHOT.read_text(encoding="utf-8"))
    fresh = [item for item in snapshots if _utc(str(item["last_seen_at"])) >= cutoff]
    by_gate = Counter(str(item["gate_name"]) for item in snapshots)
    missing_required = [name for name in REQUIRED_GATES if name not in by_gate]
    latest_seen = max((_utc(str(item["last_seen_at"])) for item in snapshots), default=None)
    earliest_seen = min((_utc(str(item["last_seen_at"])) for item in snapshots), default=None)

    uri = f"{DATABASE.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        counts = {
            "historical_alpha_records": con.execute(
                "SELECT COUNT(*) FROM legacy_alphas"
            ).fetchone()[0],
            "unique_expressions": con.execute(
                "SELECT COUNT(*) FROM alpha_expression_features"
            ).fetchone()[0],
            "behavior_clusters": con.execute(
                "SELECT COUNT(*) FROM alpha_behavior_clusters"
            ).fetchone()[0],
            "lineage_records": con.execute(
                "SELECT COUNT(*) FROM alpha_lineage"
            ).fetchone()[0],
            "return_series": con.execute(
                "SELECT COUNT(DISTINCT expression_id) FROM alpha_daily_returns"
            ).fetchone()[0],
            "settings_trials": con.execute(
                "SELECT COUNT(*) FROM settings_trials"
            ).fetchone()[0],
            "simulation_requests": con.execute(
                "SELECT COUNT(*) FROM simulation_requests"
            ).fetchone()[0],
            "submit_queue_items": con.execute(
                "SELECT COUNT(*) FROM consultant_submit_queue"
            ).fetchone()[0],
        }
        triage = {name: 0 for name in ("RECHECK", "REPAIR", "SEED_ONLY", "ARCHIVE")}
        triage.update(
            _query_dict(
                con,
                "SELECT classification,COUNT(*) FROM legacy_triage_results GROUP BY classification",
            )
        )
        triage_reasons = _query_dict(
            con,
            "SELECT reason,COUNT(*) FROM legacy_triage_results GROUP BY reason ORDER BY COUNT(*) DESC",
        )
        platform_correlation = {
            f"{name}:{result}": int(count)
            for name, result, count in con.execute(
                """SELECT upper(name),upper(result),COUNT(*) FROM alpha_check_events
                WHERE upper(name) IN ('SELF_CORRELATION','PROD_CORRELATION','PRODUCTION_CORRELATION')
                GROUP BY upper(name),upper(result) ORDER BY upper(name),upper(result)"""
            )
        }
        family_rows = [
            {
                "family": str(family or "UNCLASSIFIED"),
                "historical_alpha_count": int(total),
                "simulation_count": 0,
                "basic_gate_pass_count": 0,
                "quality_buffer_pass_count": 0,
                "final_pass_count": 0,
                "passes_per_100_simulations": "N/A",
                "note": "Shadow Run blocked before seed selection and simulation",
            }
            for family, total in con.execute(
                "SELECT family,COUNT(*) FROM legacy_alphas GROUP BY family ORDER BY family"
            )
        ]
        cluster_rows = [
            {
                "cluster_id": cluster_id,
                "behavior_signature": behavior_signature,
                "medoid_legacy_id": medoid_legacy_id,
                "member_count": member_count,
                "selected_seed": 0,
                "recheck_candidates_selected": 0,
                "selection_reason": "RUN_BLOCKED_GATE_SNAPSHOT_STALE_OR_MISSING",
            }
            for cluster_id, behavior_signature, medoid_legacy_id, member_count in con.execute(
                """SELECT cluster_id,behavior_signature,medoid_legacy_id,member_count
                FROM alpha_behavior_clusters ORDER BY cluster_id"""
            )
        ]

    blockers = [
        "GATE_SNAPSHOT_STALE_OR_MISSING",
        "REQUIRED_CORRELATION_GATE_SNAPSHOT_MISSING",
        "LOCAL_RETURN_SERIES_MISSING",
        "SHADOW_RUN_CLI_CONTRACT_UNSUPPORTED",
    ]
    summary = {
        "run_type": "Consultant Alpha Factory Shadow Run",
        "status": "BLOCKED_BEFORE_SEED_SELECTION",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "execute_submit": False,
        "submit_endpoint_calls": 0,
        "requested_mode": {
            "seed_source": "legacy",
            "cluster_medoid_only": True,
            "settings_search": "local",
            "fail_closed": True,
            "max_recheck_candidates_per_cluster": 1,
            "max_offspring_per_seed": 8,
            "settings_cartesian_product": False,
        },
        "gate_snapshot": {
            "path": "gate_snapshot.json",
            "source": "local 总alpha.csv (not a live platform refresh)",
            "snapshot_count": len(snapshots),
            "fresh_snapshot_count_24h": len(fresh),
            "freshness_hours": FRESHNESS_HOURS,
            "earliest_last_seen_at": earliest_seen.isoformat().replace("+00:00", "Z")
            if earliest_seen
            else None,
            "latest_last_seen_at": latest_seen.isoformat().replace("+00:00", "Z")
            if latest_seen
            else None,
            "gate_name_distribution": dict(sorted(by_gate.items())),
            "missing_required_gate_names": missing_required,
            "usable_for_submission_stage": False,
        },
        "inventory": counts,
        "triage": triage,
        "triage_reason_distribution": triage_reasons,
        "run_metrics": {
            "seeds_selected": 0,
            "offspring_generated": 0,
            "prescreen_rejected": 0,
            "simulations": 0,
            "basic_gate_pass": 0,
            "consultant_quality_buffer_pass": 0,
            "self_correlation_explicit_pass": 0,
            "self_correlation_pending": 0,
            "final_dry_run_candidates": 0,
            "blocked_candidate_rows": 0,
        },
        "local_max_correlation_distribution": {
            "candidate_evaluations": 0,
            "return_series_available": counts["return_series"],
            "status": "NOT_EVALUATED_NO_CANDIDATES_AND_NO_RETURN_SERIES",
        },
        "platform_correlation_historical_distribution": platform_correlation,
        "dry_run": {
            "queue_before_evaluation": {},
            "candidates": 0,
            "allowed": 0,
            "blocked": 0,
            "endpoint_calls": 0,
        },
        "run_blockers": blockers,
        "unsupported_shadow_run_arguments": [
            "--execute-submit=false",
            "--seed-source=legacy",
            "--cluster-medoid-only",
            "--settings-search=local",
            "--fail-closed",
        ],
        "recommend_expand_shadow_budget": False,
        "recommendation": (
            "Do not expand the simulation budget. First obtain fresh live platform gate "
            "snapshots (including SELF_CORRELATION and PROD_CORRELATION), ingest daily "
            "returns, and implement the requested shadow-run CLI contract."
        ),
    }

    (ROOT / "shadow_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        ROOT / "shadow_run_candidates.csv",
        [
            "candidate_id",
            "seed_legacy_id",
            "cluster_id",
            "family",
            "expression",
            "settings_profile",
            "simulation_id",
            "basic_gate_pass",
            "quality_buffer_pass",
            "local_correlation_status",
            "platform_self_correlation_status",
            "platform_production_correlation_status",
            "complete_pass_evidence_json",
            "dry_run_queue_status",
        ],
        [],
    )
    _write_csv(
        ROOT / "shadow_run_blocked.csv",
        ["scope", "candidate_id", "stage", "reason", "detail", "final_queue_eligible"],
        [
            {
                "scope": "RUN",
                "candidate_id": "",
                "stage": "BEFORE_SEED_SELECTION",
                "reason": reason,
                "detail": {
                    "GATE_SNAPSHOT_STALE_OR_MISSING": "0/30 snapshots are fresh within 24 hours",
                    "REQUIRED_CORRELATION_GATE_SNAPSHOT_MISSING": ",".join(missing_required),
                    "LOCAL_RETURN_SERIES_MISSING": "alpha_daily_returns contains 0 distinct expression series",
                    "SHADOW_RUN_CLI_CONTRACT_UNSUPPORTED": "the five required shadow-run arguments were rejected by argparse",
                }[reason],
                "final_queue_eligible": False,
            }
            for reason in blockers
        ],
    )
    _write_csv(
        ROOT / "shadow_run_family_metrics.csv",
        [
            "family",
            "historical_alpha_count",
            "simulation_count",
            "basic_gate_pass_count",
            "quality_buffer_pass_count",
            "final_pass_count",
            "passes_per_100_simulations",
            "note",
        ],
        family_rows,
    )
    _write_csv(
        ROOT / "shadow_run_settings_metrics.csv",
        [
            "settings_profile",
            "simulation_count",
            "basic_gate_pass_count",
            "quality_buffer_pass_count",
            "final_pass_count",
            "passes_per_100_simulations",
            "note",
        ],
        [
            {
                "settings_profile": "NOT_RUN",
                "simulation_count": 0,
                "basic_gate_pass_count": 0,
                "quality_buffer_pass_count": 0,
                "final_pass_count": 0,
                "passes_per_100_simulations": "N/A",
                "note": "No local settings search was executed because the run failed closed",
            }
        ],
    )
    _write_csv(
        ROOT / "shadow_run_correlation_clusters.csv",
        [
            "cluster_id",
            "behavior_signature",
            "medoid_legacy_id",
            "member_count",
            "selected_seed",
            "recheck_candidates_selected",
            "selection_reason",
        ],
        cluster_rows,
    )

    platform_corr_text = (
        ", ".join(f"{key}={value}" for key, value in platform_correlation.items())
        or "无历史平台相关性 check event"
    )
    report = f"""# Consultant Alpha Factory 第一次 Shadow Run

## 结论

本次运行按 fail-closed 结束于 seed 选择之前，未执行模拟、未生成 offspring、未形成最终候选。真实 submit endpoint 调用数为 **0**。这不是零候选成功样本，而是前置证据不完整导致的受控阻断。

## Gate Snapshot

- 快照文件：`gate_snapshot.json`
- 来源：本地 `总alpha.csv`，不是 live platform refresh
- Snapshot：{len(snapshots)}；24 小时内新鲜：{len(fresh)}
- 最早/最晚 last_seen：{summary['gate_snapshot']['earliest_last_seen_at']} / {summary['gate_snapshot']['latest_last_seen_at']}
- Gate 分布：{json.dumps(dict(sorted(by_gate.items())), ensure_ascii=False)}
- 缺失必需 Gate：{', '.join(missing_required)}
- 判定：缺失或过期，提交阶段不可用

## 历史 Alpha 与 triage

| 指标 | 数量 |
|---|---:|
| 源文件扫描行（本轮 import 输出） | 18,659 |
| 历史 Alpha 记录 | {counts['historical_alpha_records']:,} |
| 唯一表达式 | {counts['unique_expressions']:,} |
| behavior cluster | {counts['behavior_clusters']:,} |
| RECHECK | {triage['RECHECK']:,} |
| REPAIR | {triage['REPAIR']:,} |
| SEED_ONLY | {triage['SEED_ONLY']:,} |
| ARCHIVE | {triage['ARCHIVE']:,} |

失败分布：`missing_metrics_or_gate={triage_reasons.get('missing_metrics_or_gate', 0):,}`，`invalid_expression={triage_reasons.get('invalid_expression', 0):,}`。每个 cluster 的 recheck candidate 实际均为 0，没有超过默认最多 1 个的限制。

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

0 个 PENDING 不是“相关性已通过”：候选生成前即被阻断，因此没有候选进入相关性检查。`alpha_daily_returns` 的可用 return series 为 {counts['return_series']}，本地 max correlation 分布不可计算；状态为 `NOT_EVALUATED_NO_CANDIDATES_AND_NO_RETURN_SERIES`。

## 平台相关性结果分布

本轮没有新模拟，因而没有获取新的平台 checks。历史 `alpha_check_events` 中的相关性结果为：{platform_corr_text}。Gate Registry 中没有 `SELF_CORRELATION` 与 `PROD_CORRELATION` snapshot，因此任何 PENDING/MISSING/UNKNOWN 均不能进入最终队列。

## Family 与 settings 效率

本轮所有 family 的模拟次数均为 0，“每 100 次模拟通过率”统一记为 `N/A`，详见 `shadow_run_family_metrics.csv`。本轮没有运行 settings profile；`shadow_run_settings_metrics.csv` 记录 `NOT_RUN`，未使用完整笛卡尔积。

## 最终候选完整证据

最终候选为 0，因此没有可列出的完整通过证据。`shadow_run_candidates.csv` 仅保留严格证据字段表头，没有伪造候选。

## Blocked 原因

1. `GATE_SNAPSHOT_STALE_OR_MISSING`：30 个 snapshot 中 24 小时内新鲜数为 0。
2. `REQUIRED_CORRELATION_GATE_SNAPSHOT_MISSING`：缺失 {', '.join(missing_required)}。
3. `LOCAL_RETURN_SERIES_MISSING`：本地 return series 为 0。
4. `SHADOW_RUN_CLI_CONTRACT_UNSUPPORTED`：当前 CLI 拒绝全部五个强制参数；现有实现只做 generate，不做模拟或报告。

候选级 blocked 集合为空，因为没有候选被生成；上述运行级 blocker 已完整写入 `shadow_run_blocked.csv`。`submit dry-run` 的实际输出为 `endpoint_calls=0, candidates=0, allowed=0, blocked=0`。

## 是否扩大预算

**不建议。** 当前瓶颈不是 64 次模拟预算，而是 Gate 新鲜度、相关性 Gate、daily returns 和 Shadow Run CLI 实现缺失。扩大预算不会产生可信通过样本。应先完成只读 live Gate/check 同步、return ingestion，并实现/验证用户指定的有界 Shadow Run 合同；之后再用现有 64 次上限做小规模运行。
"""
    (ROOT / "CONSULTANT_ALPHA_SHADOW_RUN.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
