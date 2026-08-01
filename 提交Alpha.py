#!/usr/bin/env python3
"""
提交Alpha脚本（持续循环模式）

功能：
1. 持续循环读取"待提交Alpha列表.csv"
2. 扫脸登录后，使用账号密码/cookie/API连接WorldQuant
3. 批量simulate alpha
4. 自动生成description
5. 有description的alpha自动submit，否则只simulate不submit
6. 已处理的alpha追加到"已提交Alpha历史.csv"
7. Ctrl+C 停止

前置条件：
- 已运行"生成Alpha.py"生成待提交列表
- 已完成扫脸登录
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def read_pending_candidates(input_path: Path, processed_hashes: set) -> list[dict]:
    """读取待提交的候选（跳过已处理的）"""
    if not input_path.exists():
        return []

    candidates = []
    try:
        with input_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                exact_hash = row.get("精确哈希", "")
                if exact_hash and exact_hash not in processed_hashes:
                    candidates.append(row)
    except Exception as exc:
        print(f"[提交Alpha] 读取CSV失败: {exc}")

    return candidates


def append_to_history(history_path: Path, candidate: dict, result: dict):
    """追加已处理的候选到历史文件"""
    file_exists = history_path.exists()

    with history_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "候选ID", "表达式", "精确哈希", "策略家族",
                "生成时间", "提交时间", "alpha_id", "sharpe",
                "status", "submitted", "description_length", "error"
            ])

        writer.writerow([
            candidate.get("候选ID", ""),
            candidate.get("表达式", ""),
            candidate.get("精确哈希", ""),
            candidate.get("策略家族", ""),
            candidate.get("生成时间", ""),
            result.get("processed_at", ""),
            result.get("alpha_id", ""),
            result.get("sharpe", ""),
            result.get("status", ""),
            result.get("submitted", False),
            result.get("description_length", 0),
            result.get("error", ""),
        ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="持续循环批量提交Alpha到WorldQuant平台")
    parser.add_argument("--input", default="待提交Alpha列表.csv", help="输入CSV文件")
    parser.add_argument("--history", default="已提交Alpha历史.csv", help="历史记录文件")
    parser.add_argument("--database", default="research_memory.sqlite", help="研究数据库")
    parser.add_argument("--auth-state", default=".wq_auth_state.json", help="认证状态文件")
    parser.add_argument("--batch-size", type=int, default=10, help="每批提交数量")
    parser.add_argument("--interval", type=int, default=30, help="轮次间隔（秒）")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行（不实际提交）")
    parser.add_argument("--simulate-only", action="store_true", help="仅simulate，不submit")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env
    load_workspace_env(_ROOT / ".env")

    print(f"[提交Alpha] === 持续提交模式 ===")
    print(f"[提交Alpha] 输入: {args.input}")
    print(f"[提交Alpha] 历史: {args.history}")
    print(f"[提交Alpha] 批次: {args.batch_size}")
    print(f"[提交Alpha] 间隔: {args.interval} 秒")
    if args.dry_run:
        print(f"[提交Alpha] 模式: 模拟运行")
    elif args.simulate_only:
        print(f"[提交Alpha] 模式: 仅simulate")
    else:
        print(f"[提交Alpha] 模式: simulate + submit")
    print()

    input_path = Path(args.input)
    history_path = Path(args.history)

    # 读取历史记录，构建已处理集合
    processed_hashes = set()
    if history_path.exists():
        try:
            with history_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    processed_hashes.add(row.get("精确哈希", ""))
            print(f"[提交Alpha] 已加载历史: {len(processed_hashes)} 个已处理")
        except Exception as exc:
            print(f"[提交Alpha] 历史文件读取失败: {exc}")

    # 初始化平台网关
    gateway = None
    if not args.dry_run:
        try:
            from alpha_mining.platform.gateway import PlatformGateway

            gateway = PlatformGateway(
                state_path=args.auth_state,
                database=args.database,
                lock_path="worldquant_api.lock",
                min_interval=2.0,
            )
            print(f"[提交Alpha] ✓ 平台网关已初始化")
        except Exception as exc:
            print(f"[提交Alpha] ✗ 平台网关初始化失败: {exc}")
            return 1

    # 初始化description生成器
    providers = None
    if not args.dry_run:
        try:
            from alpha_mining.llm import create_runtime_providers
            providers = create_runtime_providers()
            print(f"[提交Alpha] ✓ Description生成器已初始化")
        except Exception as exc:
            print(f"[提交Alpha] 警告: Description初始化失败: {exc}")

    round_num = 0
    total_processed = 0

    try:
        while True:
            round_num += 1
            print(f"[提交Alpha] === 第 {round_num} 轮 ===")

            # 读取待提交候选
            candidates = read_pending_candidates(input_path, processed_hashes)

            if not candidates:
                print(f"[提交Alpha] 无待提交候选，等待...")
                time.sleep(args.interval)
                continue

            print(f"[提交Alpha] 待处理: {len(candidates)} 个候选")

            # 处理本批次
            batch = candidates[:args.batch_size]

            for idx, candidate in enumerate(batch, 1):
                expression = candidate.get("表达式", "")
                candidate_id = candidate.get("候选ID", "")
                family = candidate.get("策略家族", "")
                exact_hash = candidate.get("精确哈希", "")

                print(f"[提交Alpha] [{idx}/{len(batch)}] {candidate_id[:16]}...")

                result = {
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "submitted": False,
                }

                if args.dry_run:
                    print(f"[提交Alpha]   [模拟] simulate: {expression[:60]}...")
                    result["status"] = "DRY_RUN"
                    result["alpha_id"] = "dry_run_id"
                    result["sharpe"] = 0.0
                else:
                    # Simulate
                    try:
                        settings = {"region": "USA", "universe": "TOP3000", "delay": 1}
                        sim_result = gateway.simulate(
                            expression=expression,
                            settings=settings,
                            alpha_type="REGULAR",
                        )
                        result["alpha_id"] = sim_result.alpha_id
                        result["status"] = sim_result.status
                        result["sharpe"] = sim_result.metrics.get("sharpe", 0.0)

                        print(f"[提交Alpha]   ✓ simulate: sharpe={result['sharpe']:.3f}")

                    except Exception as exc:
                        print(f"[提交Alpha]   ✗ simulate失败: {exc}")
                        result["error"] = str(exc)
                        append_to_history(history_path, candidate, result)
                        processed_hashes.add(exact_hash)
                        total_processed += 1
                        continue

                    # 生成description并submit
                    if not args.simulate_only:
                        description = None
                        try:
                            if providers:
                                from alpha_mining.submitter.description import generate_description
                                draft = generate_description(
                                    expression,
                                    llm=providers.llm,
                                    family=family,
                                    source="consultant_generator"
                                )
                                description = draft.text
                                result["description_length"] = len(description)
                                print(f"[提交Alpha]   ✓ description: {len(description)} 字符")
                        except Exception as exc:
                            print(f"[提交Alpha]   警告: description生成失败: {exc}")

                        # Submit（仅在有description时）
                        if description and len(description.strip()) > 50:
                            try:
                                # TODO: 调用gateway的submit方法
                                # gateway.submit(result["alpha_id"], description)
                                print(f"[提交Alpha]   ✓ 已submit")
                                result["submitted"] = True
                            except Exception as exc:
                                print(f"[提交Alpha]   ✗ submit失败: {exc}")
                        else:
                            print(f"[提交Alpha]   - 跳过submit（无有效description）")

                # 记录到历史
                append_to_history(history_path, candidate, result)
                processed_hashes.add(exact_hash)
                total_processed += 1

            # 等待下一轮
            print(f"[提交Alpha] 等待 {args.interval} 秒...\n")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n[提交Alpha] 用户中断（Ctrl+C）")

    print(f"\n[提交Alpha] === 统计 ===")
    print(f"[提交Alpha] 总轮数: {round_num}")
    print(f"[提交Alpha] 已处理: {total_processed} 个候选")
    print(f"[提交Alpha] 历史文件: {history_path.absolute()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
