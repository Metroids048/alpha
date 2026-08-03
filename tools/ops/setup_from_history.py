#!/usr/bin/env python3
"""
setup_from_history.py — 一键从历史数据初始化优化结果

运行本脚本一次后：
  1. 历史数据进入 vNext Legacy Knowledge Lake，并由 Dynamic Gate Registry 分层
  2. 生成只读候选报告，不再写旧版直接重提队列
  3. 打印 diversity_fast_signal_penalty 推荐值
     → 把它加到 run_pipeline_loop.py 的启动参数里

用法:
    python setup_from_history.py
    python setup_from_history.py --feedback alpha_submission_feedback.csv --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FEEDBACK_CSV = ROOT / "alpha_submission_feedback.csv"


def main() -> int:
    p = argparse.ArgumentParser(description="从历史数据初始化优化结果")
    p.add_argument("--feedback", default=str(FEEDBACK_CSV), help="历史反馈CSV路径")
    p.add_argument(
        "--database",
        default=str(ROOT / "research_memory.sqlite"),
        help="vNext Research Memory",
    )
    p.add_argument("--config", default=str(ROOT / "alpha_mining" / "config.yaml"))
    p.add_argument("--dry-run", action="store_true", help="只分析，不写文件")
    args = p.parse_args()

    feedback_path = Path(args.feedback)
    if not feedback_path.is_file():
        print(f"[setup] 找不到反馈文件: {feedback_path}")
        return 2

    # ── 步骤1：失败率统计 ─────────────────────────────────────────────────────
    print("\n[setup] === 步骤1: 分析历史失败率 ===")
    from alpha_mining.analysis.failure_stats import (
        compute_failure_stats,
        print_stats_report,
    )

    stats = compute_failure_stats(feedback_path)
    print_stats_report(stats)

    penalty = stats["recommendations"]["recommended_fast_signal_penalty"]
    corr_delta = stats["recommendations"]["corr_delta_fast_minus_slow"]
    ladder_delta = stats["recommendations"]["ladder_delta_fast_minus_slow"]
    hyp = stats["hypotheses_supported"]

    print("\n[setup] === 推荐 pipeline 参数 ===")
    print(f"  diversity_fast_signal_penalty = {penalty}")
    if penalty > 0:
        print(
            f"  (快信号相关失败率比慢信号高 {corr_delta:.1%}，梯度失败率高 {ladder_delta:.1%})"
        )
        print(
            f"  在 run_pipeline_loop.py 启动时加上: --diversity-fast-signal-penalty {penalty}"
        )
    else:
        print("  (快/慢信号失败率差异不显著，暂不启用惩罚)")

    if hyp.get("usa_top3000_dominates_sample"):
        print(
            "\n  [建议] USA/TOP3000 占样本绝大多数 → 考虑在启动时加 --exploration-region-share 0.2"
        )

    # ── 步骤2：旧 alpha 进入 vNext Knowledge Lake ────────────────────────────
    print("\n[setup] === 步骤2: vNext Legacy Knowledge Lake ===")
    if args.dry_run:
        print("  [dry-run] 未写数据库、报告或提交队列")
    else:
        from alpha_mining.legacy.importer import LegacyImporter
        from alpha_mining.legacy.service import report_database, triage_database
        from alpha_mining.policy.consultant_policy import ConsultantPolicy
        from alpha_mining.storage.migrations import migrate

        database = Path(args.database)
        migrate(database)
        policy = ConsultantPolicy.from_file(args.config)
        imported = LegacyImporter(database).import_sources([feedback_path])
        triaged = triage_database(
            database,
            near_pass_ratio=policy.near_pass_ratio,
            gate_freshness_hours=policy.gate_freshness_hours,
        )
        reports = report_database(database, ROOT / "consultant_reports")
        print(
            f"  导入={imported.rows_scanned} clusters={triaged.clusters} reports={reports}"
        )
        print("  旧版 alpha_resim_queue.csv 未写入；真实提交只能走 vNext guard。")

    # ── 步骤3：生成 pipeline 启动命令示例 ─────────────────────────────────────
    print("\n[setup] === 步骤3: 推荐 run_pipeline_loop.py 启动命令 ===")
    extra_args: list[str] = []
    if penalty > 0:
        extra_args.append(f"--diversity-fast-signal-penalty {penalty}")
    if hyp.get("usa_top3000_dominates_sample"):
        extra_args.append("--exploration-region-share 0.2")
    # --ladder-check-enabled activates local daily-return path (zero extra sim quota)
    extra_args.append("--ladder-check-enabled")

    passthrough = ("-- " + " ".join(extra_args)) if extra_args else ""
    cmd = f"python run_pipeline_loop.py {passthrough}".strip()
    print(f"  {cmd}")
    print()
    print("  各参数含义:")
    if penalty > 0:
        print(f"    --diversity-fast-signal-penalty {penalty}")
        print(
            f"      快信号排名惩罚：基于历史数据计算，相关失败率差={corr_delta:.1%}，梯度失败率差={ladder_delta:.1%}"
        )
    if hyp.get("usa_top3000_dominates_sample"):
        print("    --exploration-region-share 0.2")
        print("      20% 流量分流到 EUR/ASI/CHN 等非USA region，降低拥挤度")
    print("    --ladder-check-enabled")
    print(
        "      启用逐年 Sharpe 一致性检查（有 daily returns 时本地计算，无需额外平台请求）"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
