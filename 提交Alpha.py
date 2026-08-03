#!/usr/bin/env python3
"""
提交Alpha脚本（仅消费 READY Alpha 的受保护提交入口）

功能：
1. 登录检查/自动续期（照抄 启动Alpha主线.py 的逻辑）
2. 持续循环读取"待提交Alpha列表.csv"
3. 调用已有 CLI 子命令：sync-ledger / description backfill / submit dry-run / submit execute
5. 历史记录如实记录真实状态（不允许假成功）
6. Ctrl+C 停止

前置条件：
- 已运行"生成Alpha.py"生成待提交列表
- WQ_USERNAME / WQ_PASSWORD 已设置在 .env 中
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _auth_state_path(args) -> Path:
    """解析认证状态文件路径"""
    path = Path(args.auth_state)
    return path if path.is_absolute() else _ROOT / path


def _read_auth_status(path: Path) -> str:
    """读取认证状态"""
    try:
        from alpha_mining.auth.session_manager import auth_state_status

        return str(auth_state_status(path)).upper()
    except Exception:
        return "UNKNOWN"


def _ensure_fresh_auth(args) -> int:
    """确保认证状态是 FRESH，否则尝试登录（照抄 启动Alpha主线.py 的逻辑）"""
    state_path = _auth_state_path(args)
    status = _read_auth_status(state_path)

    print(f"[提交Alpha] 认证状态: {status} ({state_path.name})")

    if status == "FRESH":
        print(f"[提交Alpha] ✓ 认证状态有效，无需登录\n")
        return 0

    print(f"[提交Alpha] 认证状态不是 FRESH，尝试续期...")

    profile_dir = _ROOT / args.profile_dir
    base_cmd = [
        sys.executable,
        "-m",
        "alpha_mining",
        "platform",
        "browser-login",
        "--auth-state-file",
        str(state_path),
        "--profile-dir",
        str(profile_dir),
    ]

    # 先尝试 headless 模式（快速）
    print(f"[提交Alpha] 尝试 headless 登录...")
    headless_rc = subprocess.run(
        [*base_cmd, "--headless", "--timeout", "30"],
        cwd=str(_ROOT),
        check=False,
    ).returncode

    if headless_rc == 0:
        print(f"[提交Alpha] ✓ headless 登录成功\n")
        return 0

    # headless 失败，退化到 headed 模式（需要用户扫脸）
    print(f"[提交Alpha] headless 失败，打开浏览器进行人工登录（需要扫脸）...")
    print(f"[提交Alpha] 请在浏览器中完成登录，超时 300 秒")
    headed_rc = subprocess.run(
        [*base_cmd, "--timeout", "300"],
        cwd=str(_ROOT),
        check=False,
    ).returncode

    if headed_rc == 0:
        print(f"[提交Alpha] ✓ headed 登录成功\n")
        return 0

    print(f"[提交Alpha] ✗ 登录失败，退出")
    return headed_rc


def read_pending_candidates(input_path: Path, processed_hashes: set) -> list[dict]:
    """只读取已有 alpha_id 且质量状态 READY 的记录。"""
    if not input_path.exists():
        return []

    candidates = []
    try:
        with input_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                exact_hash = row.get("exact_hash", row.get("精确哈希", ""))
                alpha_id = row.get("alpha_id", "")
                quality_status = row.get("quality_status", "")
                if alpha_id and quality_status == "READY_TO_SUBMIT" and exact_hash and exact_hash not in processed_hashes:
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
                "候选ID",
                "表达式",
                "精确哈希",
                "策略家族",
                "生成时间",
                "处理时间",
                "alpha_id",
                "status",
                "sharpe",
                "fitness",
                "turnover",
                "error",
            ])

        writer.writerow([
            candidate.get("候选ID", ""),
            candidate.get("表达式", ""),
            candidate.get("精确哈希", ""),
            candidate.get("策略家族", ""),
            candidate.get("生成时间", ""),
            result.get("processed_at", ""),
            result.get("alpha_id", ""),
            result.get("status", ""),
            result.get("sharpe", ""),
            result.get("fitness", ""),
            result.get("turnover", ""),
            result.get("error", ""),
        ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="持续循环批量提交Alpha到WorldQuant平台")
    parser.add_argument("--input", default="待提交Alpha列表.csv", help="输入CSV文件")
    parser.add_argument("--history", default="已处理Alpha历史.csv", help="历史记录文件")
    parser.add_argument("--database", default="数据/本地运行产物/数据库/research_memory.sqlite", help="研究数据库")
    parser.add_argument("--config", default="alpha_mining/config.yaml", help="配置文件路径")
    parser.add_argument("--auth-state", default=".wq_auth_state.json", help="认证状态文件")
    parser.add_argument("--profile-dir", default=".wq_browser_profile", help="浏览器配置目录")
    parser.add_argument("--batch-size", type=int, default=20, help="每批 READY Alpha 数量")
    parser.add_argument("--max-submit", type=int, default=20, help="每轮最大提交数")
    parser.add_argument("--interval", type=int, default=30, help="轮次间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=0, help="最大轮数（0=无限循环）")
    parser.add_argument("--允许提交", action="store_true", help="允许真实提交（默认 False，加上才会跑 submit execute --execute-submit）")
    parser.add_argument("--确认短语", default="I_UNDERSTAND_REAL_SUBMISSION", help="提交确认短语")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")

    print(f"[提交Alpha] === 持续提交模式 ===")
    print(f"[提交Alpha] 输入: {args.input}")
    print(f"[提交Alpha] 历史: {args.history}")
    print(f"[提交Alpha] 数据库: {args.database}")
    print(f"[提交Alpha] 批次: {args.batch_size}")
    print(f"[提交Alpha] 间隔: {args.interval} 秒")
    if args.允许提交:
        print(f"[提交Alpha] 模式: 台账同步 + description + 真实提交")
        print(f"[提交Alpha] ⚠️  真实提交已启用！")
    else:
        print(f"[提交Alpha] 模式: 台账同步 + description + dry-run（不真实提交）")
    print()

    # 登录检查/自动续期
    auth_rc = _ensure_fresh_auth(args)
    if auth_rc != 0:
        return auth_rc

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

    round_num = 0
    total_processed = 0

    try:
        while True:
            round_num += 1

            if args.max_rounds > 0 and round_num > args.max_rounds:
                print(f"\n[提交Alpha] 已达到最大轮数 {args.max_rounds}，停止")
                break

            print(f"[提交Alpha] === 第 {round_num} 轮 ===")

            # 读取待提交候选
            candidates = read_pending_candidates(input_path, processed_hashes)

            if not candidates:
                print(f"[提交Alpha] 无待提交候选，等待...")
                time.sleep(args.interval)
                continue

            print(f"[提交Alpha] 待处理: {len(candidates)} 个候选")

            # 处理本批次
            batch = candidates[: args.batch_size]

            for idx, candidate in enumerate(batch, 1):
                candidate_id = candidate.get("candidate_id", candidate.get("候选ID", ""))
                exact_hash = candidate.get("exact_hash", candidate.get("精确哈希", ""))

                print(f"[提交Alpha] [{idx}/{len(batch)}] {candidate_id[:32]}...")

                result = {
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "alpha_id": candidate.get("alpha_id", ""),
                    "status": "READY_TO_SUBMIT",
                    "sharpe": "",
                    "fitness": "",
                    "turnover": "",
                    "error": "",
                }

                print(f"[提交Alpha]   使用已有 alpha_id={result['alpha_id']}，不重新 simulate")
                append_to_history(history_path, candidate, result)
                processed_hashes.add(exact_hash)
                total_processed += 1

            # 对已有 Alpha 继续执行台账同步 / description / submit
            if candidates:
                print(f"\n[提交Alpha] === 台账同步 / Description / Submit ===")

                # 1. 台账同步
                print(f"[提交Alpha] 执行: platform sync-ledger...")
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "alpha_mining",
                            "platform",
                            "sync-ledger",
                            "--database",
                            args.database,
                            "--auth-state-file",
                            str(_auth_state_path(args)),
                            "--status",
                            "UNSUBMITTED",
                        ],
                        cwd=str(_ROOT),
                        check=True,
                    )
                    print(f"[提交Alpha] ✓ 台账同步完成")
                except subprocess.CalledProcessError as exc:
                    print(f"[提交Alpha] ✗ 台账同步失败: {exc}")

                # 2. Description backfill
                print(f"[提交Alpha] 执行: description backfill...")
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "alpha_mining",
                            "description",
                            "backfill",
                            "--database",
                            args.database,
                            "--execute",
                            "--confirm",
                            args.确认短语,
                        ],
                        cwd=str(_ROOT),
                        check=True,
                    )
                    print(f"[提交Alpha] ✓ description backfill 完成")
                except subprocess.CalledProcessError as exc:
                    print(f"[提交Alpha] ✗ description backfill 失败: {exc}")

                # 3. Submit dry-run
                print(f"[提交Alpha] 执行: submit dry-run...")
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "alpha_mining",
                            "submit",
                            "dry-run",
                            "--database",
                            args.database,
                            "--config",
                            args.config,
                        ],
                        cwd=str(_ROOT),
                        check=True,
                    )
                    print(f"[提交Alpha] ✓ submit dry-run 完成")
                except subprocess.CalledProcessError as exc:
                    print(f"[提交Alpha] ✗ submit dry-run 失败: {exc}")

                # 4. Submit execute（仅在用户显式加了 --允许提交 时执行）
                if args.允许提交:
                    print(f"\n[提交Alpha] ⚠️  执行: submit execute --execute-submit（真实提交）...")
                    print(f"[提交Alpha] 重要提示：真正提交前，请确保 {args.config} 中 consultant: 段落下的 execute_submit 已设为 true")
                    print(f"[提交Alpha] 如果未设置，提交将被配置文件拒绝（双重保险）\n")
                    try:
                        subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "alpha_mining",
                                "submit",
                                "execute",
                                "--database",
                                args.database,
                                "--config",
                                args.config,
                                "--confirm",
                                args.确认短语,
                                "--auth-state-file",
                                str(_auth_state_path(args)),
                                "--max-submit",
                                str(args.max_submit),
                                "--execute-submit",
                            ],
                            cwd=str(_ROOT),
                            check=True,
                        )
                        print(f"[提交Alpha] ✓ submit execute 完成")
                    except subprocess.CalledProcessError as exc:
                        print(f"[提交Alpha] ✗ submit execute 失败: {exc}")
                else:
                    print(f"[提交Alpha] - 跳过真实提交（未加 --允许提交 参数）")

                print()

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
