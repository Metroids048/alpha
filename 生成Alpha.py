#!/usr/bin/env python3
"""
Alpha生成脚本（持续循环模式）

功能：
1. 持续循环使用LLM（DeepSeek）生成alpha候选
2. 每轮生成后追加到CSV文件
3. 自动去重和验证
4. Ctrl+C 停止

输出文件：待提交Alpha列表.csv（累积模式）
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def append_candidates_to_csv(output_path: Path, candidates: list, generation_time: str) -> int:
    """追加候选到CSV，返回实际写入的行数"""
    existing = set()

    # 读取现有的精确哈希，用于去重
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing.add(row.get("精确哈希", ""))
        except Exception:
            pass

    # 过滤已存在的候选
    new_candidates = [c for c in candidates if c.exact_hash not in existing]

    if not new_candidates:
        return 0

    # 追加模式写入
    file_exists = output_path.exists()
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        # 如果文件不存在，先写表头
        if not file_exists:
            writer.writerow([
                "候选ID",
                "主题ID",
                "假设ID",
                "研究家族",
                "策略家族",
                "变异类型",
                "机制",
                "数据集",
                "表达式",
                "精确哈希",
                "参数骨架",
                "字段骨架",
                "生成时间",
            ])

        # 写入新候选
        for candidate in new_candidates:
            writer.writerow([
                candidate.candidate_id,
                candidate.topic_id,
                candidate.hypothesis_id,
                candidate.research_family,
                candidate.strategy_family,
                candidate.mutation_type,
                candidate.mechanism,
                candidate.dataset,
                candidate.expression,
                candidate.exact_hash,
                candidate.parameter_skeleton,
                candidate.field_skeleton,
                generation_time,
            ])

    return len(new_candidates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="持续循环生成Alpha候选（LLM驱动）")
    parser.add_argument("--database", default="research_memory.sqlite", help="研究数据库路径")
    parser.add_argument("--output", default="待提交Alpha列表.csv", help="输出CSV文件路径（追加模式）")
    parser.add_argument("--batch-size", type=int, default=50, help="每轮生成数量")
    parser.add_argument("--interval", type=int, default=10, help="轮次间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=0, help="最大轮数（0=无限循环）")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env
    load_workspace_env(_ROOT / ".env")

    print(f"[生成Alpha] === 持续生成模式 ===")
    print(f"[生成Alpha] 数据库: {args.database}")
    print(f"[生成Alpha] 输出: {args.output} (追加模式)")
    print(f"[生成Alpha] 每轮: {args.batch_size} 个候选")
    print(f"[生成Alpha] 间隔: {args.interval} 秒")
    if args.max_rounds > 0:
        print(f"[生成Alpha] 最大轮数: {args.max_rounds}")
    else:
        print(f"[生成Alpha] 模式: 无限循环（Ctrl+C停止）")
    print()

    # 初始化生成服务
    from alpha_mining.generation.service import CandidateGenerationService

    llm_generator = None
    try:
        from alpha_mining.llm import create_runtime_providers
        from alpha_mining.generator.llm_consultant_bridge import LLMConsultantBridge

        providers = create_runtime_providers()
        llm_generator = LLMConsultantBridge(
            database=args.database,
            llm=providers.llm,
            max_per_hypothesis=8,
        )

        print("[生成Alpha] ✓ LLM生成服务已初始化（DeepSeek + ExpressionGenerator）")
    except Exception as exc:
        print(f"[生成Alpha] 警告: LLM初始化失败: {exc}")
        print(f"[生成Alpha] 将使用ConsultantGenerator（模板生成）")
        llm_generator = None

    candidate_service = CandidateGenerationService(
        args.database,
        generator=llm_generator,
    )

    output_path = Path(args.output)
    round_num = 0
    total_generated = 0
    consecutive_empty_rounds = 0  # 连续空轮计数器

    try:
        while True:
            round_num += 1

            if args.max_rounds > 0 and round_num > args.max_rounds:
                print(f"\n[生成Alpha] 已达到最大轮数 {args.max_rounds}，停止")
                break

            print(f"[生成Alpha] === 第 {round_num} 轮 ===")

            # 生成候选
            try:
                batch = candidate_service.generate(limit=args.batch_size)
                generation_time = datetime.now(timezone.utc).isoformat()

                if args.verbose:
                    print(f"[生成Alpha]   生成: {len(batch.candidates)} 个候选")
                    print(f"[生成Alpha]   主题: {batch.selected_topic_ids}")
                    print(f"[生成Alpha]   策略: {batch.selected_families}")
                    if batch.rejected_by_reason:
                        print(f"[生成Alpha]   拒绝: {batch.rejected_by_reason}")

                # 始终显示拒绝原因（关键诊断信息）
                if batch.rejected_by_reason:
                    total_rejected = sum(batch.rejected_by_reason.values())
                    print(f"[生成Alpha]   本轮拒绝 {total_rejected} 个: {batch.rejected_by_reason}")

                # 显示前3个候选表达式（让用户看到实际生成内容）
                if batch.candidates and args.verbose:
                    print(f"[生成Alpha]   示例表达式:")
                    for i, c in enumerate(batch.candidates[:3], 1):
                        print(f"[生成Alpha]     {i}. {c.expression[:80]}...")
            except Exception as exc:
                print(f"[生成Alpha] ✗ 生成失败: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(args.interval)
                continue

            if not batch.candidates:
                consecutive_empty_rounds += 1
                print(f"[生成Alpha] 本轮未生成候选（连续第 {consecutive_empty_rounds} 轮空轮）")
                if batch.deferred_reason:
                    print(f"[生成Alpha] 原因: {batch.deferred_reason}")
                if batch.rejected_by_reason:
                    print(f"[生成Alpha] 拒绝统计: {batch.rejected_by_reason}")

                # 连续10轮空轮，警告用户
                if consecutive_empty_rounds >= 10:
                    print(f"\n[生成Alpha] ⚠️  警告: 已连续 {consecutive_empty_rounds} 轮未生成候选")
                    print(f"[生成Alpha] 可能原因:")
                    print(f"[生成Alpha]   - 模板生成器组合空间耗尽")
                    print(f"[生成Alpha]   - LLM生成失败或未启用")
                    print(f"[生成Alpha]   - 去重过滤过于严格")

                # 连续30轮空轮，自动停止
                if consecutive_empty_rounds >= 30:
                    print(f"\n[生成Alpha] ✗ 已连续 {consecutive_empty_rounds} 轮未生成候选，自动停止")
                    print(f"[生成Alpha] 建议:")
                    print(f"[生成Alpha]   1. 检查LLM是否正常工作")
                    print(f"[生成Alpha]   2. 增加研究假设数量")
                    print(f"[生成Alpha]   3. 放宽去重策略")
                    break

                time.sleep(args.interval)
                continue
            else:
                consecutive_empty_rounds = 0  # 重置连续空轮计数器

            # 追加到CSV
            try:
                new_count = append_candidates_to_csv(output_path, batch.candidates, generation_time)
                total_generated += new_count
                print(f"[生成Alpha] ✓ 新增 {new_count} 个候选（总计 {total_generated}）")
            except Exception as exc:
                print(f"[生成Alpha] ✗ CSV写入失败: {exc}")

            # 等待下一轮
            if args.max_rounds == 0 or round_num < args.max_rounds:
                print(f"[生成Alpha] 等待 {args.interval} 秒...\n")
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n[生成Alpha] 用户中断（Ctrl+C）")

    print(f"\n[生成Alpha] === 统计 ===")
    print(f"[生成Alpha] 总轮数: {round_num}")
    print(f"[生成Alpha] 累积生成: {total_generated} 个候选")
    print(f"[生成Alpha] 输出文件: {output_path.absolute()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
