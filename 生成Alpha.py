#!/usr/bin/env python3
"""
Alpha生成脚本（持续循环模式，复用 v50 真实引擎）

功能：
1. 持续循环使用 auto_alpha_pipeline_rebuilt_v50 的真实生成引擎（不需要登录，前提是本地字段缓存热）
2. 每轮生成后追加到CSV文件
3. 自动去重和验证
4. 无限循环（Ctrl+C 停止）

输出文件：待提交Alpha列表.csv（累积模式，供 提交Alpha.py 消费）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def append_candidates_to_csv(
    output_path: Path,
    payloads: list[dict],
    generation_time: str,
    existing_hashes: set[str],
) -> int:
    """追加候选到CSV（跨运行持久化去重），返回实际写入的行数"""
    new_payloads = [p for p in payloads if p.get("meta", {}).get("exact_hash") not in existing_hashes]

    if not new_payloads:
        return 0

    file_exists = output_path.exists()
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "候选ID",
                "策略家族",
                "来源",
                "分数",
                "表达式",
                "模拟设置JSON",
                "变体序号",
                "精确哈希",
                "参数骨架",
                "字段骨架",
                "生成时间",
            ])

        for payload in new_payloads:
            meta = payload.get("meta", {})
            settings_json = json.dumps(payload.get("settings", {}), ensure_ascii=False)
            writer.writerow([
                meta.get("profile", ""),
                meta.get("family", ""),
                meta.get("source", ""),
                meta.get("candidate_score", 0.0),
                payload.get("regular", ""),
                settings_json,
                meta.get("variant", 0),
                meta.get("exact_hash", ""),
                meta.get("parameter_skeleton", ""),
                meta.get("field_skeleton", ""),
                generation_time,
            ])
            existing_hashes.add(meta.get("exact_hash", ""))

    return len(new_payloads)


def load_existing_hashes(output_path: Path) -> set[str]:
    """读取CSV中已有的精确哈希，用于去重"""
    if not output_path.exists():
        return set()

    existing = set()
    try:
        with output_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                h = row.get("精确哈希", "")
                if h:
                    existing.add(h)
    except Exception as exc:
        print(f"[生成Alpha] 警告: 读取现有哈希失败: {exc}")

    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="持续循环生成Alpha候选（复用 v50 引擎，离线模式）"
    )
    parser.add_argument("--output", default="待提交Alpha列表.csv", help="输出CSV文件路径（追加模式）")
    parser.add_argument("--batch-size", type=int, default=300, help="每轮目标候选数（对应 min_candidates_floor）")
    parser.add_argument("--max-payloads", type=int, default=600, help="每轮最大payload数")
    parser.add_argument("--interval", type=int, default=15, help="轮次间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=0, help="最大轮数（0=无限循环）")
    parser.add_argument("--preset", default="", help="预设配置名（传给 PipelineConfig.preset）")
    parser.add_argument(
        "--sync-submitted-history",
        action="store_true",
        help="允许拉取平台已提交历史（需要已登录，默认关闭以保证离线）",
    )
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")

    print(f"[生成Alpha] === 持续生成模式（v50 引擎）===")
    print(f"[生成Alpha] 输出: {args.output} (追加模式)")
    print(f"[生成Alpha] 每轮目标: {args.batch_size} 个候选")
    print(f"[生成Alpha] 最大payload: {args.max_payloads}")
    print(f"[生成Alpha] 间隔: {args.interval} 秒")
    if args.max_rounds > 0:
        print(f"[生成Alpha] 最大轮数: {args.max_rounds}")
    else:
        print(f"[生成Alpha] 模式: 无限循环（Ctrl+C停止）")
    if args.sync_submitted_history:
        print(f"[生成Alpha] 联网模式: 已启用（拉取平台历史）")
    else:
        print(f"[生成Alpha] 离线模式: 已启用（不联网，需要本地字段缓存热）")
    print()

    # 初始化 v50 引擎
    import auto_alpha_pipeline_rebuilt_v50 as v50

    username = os.environ.get("WQ_USERNAME", "")
    password = os.environ.get("WQ_PASSWORD", "")
    if not username:
        username = "placeholder_user"
    if not password:
        password = "placeholder_pass"

    cfg = v50.PipelineConfig(username=username, password=password)
    cfg.min_candidates_floor = args.batch_size
    cfg.sync_platform_tried_before_simulate = False

    if not args.sync_submitted_history:
        cfg.library_expression_fetch_max = 0  # 关键：跳过需要登录的 /users/self/alphas 拉取
        print("[生成Alpha] ✓ 已设置 library_expression_fetch_max=0（离线模式）")

    if args.preset:
        cfg.preset = args.preset

    try:
        # Patch v50 引擎：强制启用字段/数据集磁盘缓存，忽略 TTL（完全离线模式）
        cfg.enable_fields_disk_cache = True
        cfg.fields_disk_cache_ttl_seconds = 365 * 24 * 3600  # 1年，实际上就是永久使用缓存

        pipeline = v50.WorldQuantAlphaPipeline(cfg)
        selector = v50.ProfileSelector(cfg)
        print("[生成Alpha] ✓ v50 引擎已初始化（ExpressionFactory + FieldCatalog + NearPassAmplifier）")
        print("[生成Alpha] ✓ 磁盘缓存 TTL 已设为永久（完全离线模式）")
    except Exception as exc:
        print(f"[生成Alpha] ✗ v50 引擎初始化失败: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    output_path = Path(args.output)
    existing_hashes = load_existing_hashes(output_path)
    print(f"[生成Alpha] 已加载 {len(existing_hashes)} 个历史精确哈希（去重）\n")

    round_num = 0
    total_generated = 0

    try:
        while True:
            round_num += 1

            if args.max_rounds > 0 and round_num > args.max_rounds:
                print(f"\n[生成Alpha] 已达到最大轮数 {args.max_rounds}，停止")
                break

            print(f"[生成Alpha] === 第 {round_num} 轮 ===")

            # 生成候选
            try:
                candidates, catalog = pipeline.generate_candidates()
                print(f"[生成Alpha]   生成: {len(candidates)} 个候选")
            except Exception as exc:
                print(f"[生成Alpha] ✗ 生成失败: {exc}")
                error_msg = str(exc).lower()
                if "403" in error_msg or "401" in error_msg or "cache" in error_msg:
                    print(f"[生成Alpha] 可能原因: 本地字段/数据集缓存缺失或过期")
                    print(f"[生成Alpha] 建议: 运行 提交Alpha.py 完成一次登录，它会顺带刷新本地缓存")
                import traceback
                traceback.print_exc()
                print(f"[生成Alpha] 等待 {args.interval} 秒后重试...\n")
                time.sleep(args.interval)
                continue

            if not candidates:
                print(f"[生成Alpha] 本轮未生成候选，等待 {args.interval} 秒后重试...\n")
                time.sleep(args.interval)
                continue

            # 转换为 payloads（包含完整平台 settings）
            try:
                payloads = selector.payloads_for(candidates, max_payloads=args.max_payloads)
                print(f"[生成Alpha]   转换: {len(payloads)} 个 payload")

                # 填充 exact_hash 到 meta（供去重使用）
                from alpha_mining.domain.expression_normalization import expression_identity

                for payload in payloads:
                    if "meta" not in payload:
                        payload["meta"] = {}
                    identity = expression_identity(payload.get("regular", ""))
                    payload["meta"]["exact_hash"] = identity.exact_hash
                    payload["meta"]["parameter_skeleton"] = identity.parameter_skeleton
                    payload["meta"]["field_skeleton"] = identity.field_skeleton

            except Exception as exc:
                print(f"[生成Alpha] ✗ payload转换失败: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(args.interval)
                continue

            # 追加到CSV
            try:
                generation_time = datetime.now(timezone.utc).isoformat()
                new_count = append_candidates_to_csv(output_path, payloads, generation_time, existing_hashes)
                total_generated += new_count
                print(f"[生成Alpha] ✓ 新增 {new_count} 个候选（总计 {total_generated}）")
            except Exception as exc:
                print(f"[生成Alpha] ✗ CSV写入失败: {exc}")
                import traceback
                traceback.print_exc()

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
