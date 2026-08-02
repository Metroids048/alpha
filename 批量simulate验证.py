#!/usr/bin/env python3
"""
批量Simulate验证脚本

功能：
1. 读取高质量Alpha候选.csv
2. 批量调用PlatformGateway.simulate()
3. 记录结果到simulate_results.csv
4. 支持中断续传（checkpoint）
5. 先测试10个，确认通畅后询问是否继续

使用方法：
    python 批量simulate验证.py              # 测试前10个
    python 批量simulate验证.py --full       # 全量执行
    python 批量simulate验证.py --start 10   # 从第10个开始
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


def load_candidates(csv_path: Path) -> list[dict]:
    """加载候选列表"""
    candidates = []
    try:
        with csv_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                candidates.append(row)
    except Exception as exc:
        print(f"❌ 读取CSV失败: {exc}")
        raise
    return candidates


def load_checkpoint(checkpoint_path: Path) -> set[str]:
    """加载已处理的候选ID"""
    processed = set()
    if not checkpoint_path.exists():
        return processed

    try:
        with checkpoint_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("alpha_id"):  # 只有成功的才算已处理
                    processed.add(row["候选ID"])
    except Exception:
        pass

    return processed


def append_result(results_path: Path, result: dict):
    """追加单个结果到CSV"""
    file_exists = results_path.exists()

    with results_path.open("a", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "候选ID", "策略家族", "来源", "分数", "表达式",
            "alpha_id", "status", "sharpe", "fitness", "turnover",
            "returns", "margin", "drawdown", "error", "处理时间"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(result)


def simulate_one(gateway, candidate: dict, index: int, total: int) -> dict:
    """模拟单个候选"""
    candidate_id = candidate.get("候选ID", f"unknown_{index}")
    expression = candidate.get("表达式", "")
    settings_json = candidate.get("模拟设置JSON", "{}")

    print(f"\n[{index}/{total}] {candidate_id}")
    print(f"  表达式: {expression[:80]}{'...' if len(expression) > 80 else ''}")

    result = {
        "候选ID": candidate_id,
        "策略家族": candidate.get("策略家族", ""),
        "来源": candidate.get("来源", ""),
        "分数": candidate.get("分数", ""),
        "表达式": expression,
        "alpha_id": None,
        "status": None,
        "sharpe": None,
        "fitness": None,
        "turnover": None,
        "returns": None,
        "margin": None,
        "drawdown": None,
        "error": None,
        "处理时间": datetime.now(timezone.utc).isoformat(),
    }

    try:
        settings = json.loads(settings_json)
    except json.JSONDecodeError as exc:
        result["error"] = f"settings JSON解析失败: {exc}"
        result["status"] = "ERROR"
        print(f"  ❌ settings解析失败: {exc}")
        return result

    try:
        from alpha_mining.platform.gateway import PlatformGateway

        sim_result = gateway.simulate(
            expression=expression,
            settings=settings,
            alpha_type="REGULAR",
        )

        result["alpha_id"] = sim_result.alpha_id
        result["status"] = sim_result.status

        # 提取指标
        if sim_result.metrics:
            result["sharpe"] = sim_result.metrics.get("sharpe")
            result["fitness"] = sim_result.metrics.get("fitness")
            result["turnover"] = sim_result.metrics.get("turnover")
            result["returns"] = sim_result.metrics.get("returns")
            result["margin"] = sim_result.metrics.get("margin")
            result["drawdown"] = sim_result.metrics.get("drawdown")

        print(f"  ✅ alpha_id={sim_result.alpha_id}")
        print(f"     status={sim_result.status}")
        if result["sharpe"] is not None:
            print(f"     sharpe={result['sharpe']:.4f}, fitness={result['fitness']:.4f}, turnover={result['turnover']:.4f}")

    except Exception as exc:
        result["error"] = str(exc)
        result["status"] = "ERROR"
        print(f"  ❌ 模拟失败: {exc}")

        # 识别常见错误
        error_msg = str(exc).lower()
        if "401" in error_msg or "unauthorized" in error_msg:
            print(f"     💡 认证失败，请重新登录")
            raise  # 认证失败应该终止批次
        elif "429" in error_msg or "rate limit" in error_msg:
            print(f"     💡 速率限制，等待60秒...")
            time.sleep(60)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量Simulate验证脚本")
    parser.add_argument("--input", default="高质量Alpha候选.csv", help="输入CSV文件")
    parser.add_argument("--output", default="simulate_results.csv", help="输出结果文件")
    parser.add_argument("--full", action="store_true", help="全量执行（跳过确认）")
    parser.add_argument("--start", type=int, default=0, help="起始索引（0-based）")
    parser.add_argument("--limit", type=int, default=10, help="测试模式限制数量（--full时忽略）")
    parser.add_argument("--interval", type=float, default=2.5, help="请求间隔（秒）")
    args = parser.parse_args(argv)

    from alpha_mining.common import load_workspace_env
    load_workspace_env(_ROOT / ".env")

    print("=" * 70)
    print("🧪 批量Simulate验证")
    print("=" * 70)

    # 加载候选
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ 输入文件不存在: {input_path}")
        return 1

    candidates = load_candidates(input_path)
    print(f"✅ 加载 {len(candidates)} 个候选")

    # 加载checkpoint
    output_path = Path(args.output)
    processed = load_checkpoint(output_path)
    if processed:
        print(f"📌 检测到 {len(processed)} 个已处理候选（跳过）")

    # 过滤未处理的候选
    pending = [c for c in candidates if c.get("候选ID") not in processed]
    print(f"📋 待处理: {len(pending)} 个候选")

    if not pending:
        print("✅ 所有候选已处理完成")
        return 0

    # 应用start和limit
    if args.start > 0:
        pending = pending[args.start:]
        print(f"📍 从第 {args.start + 1} 个开始")

    if not args.full:
        pending = pending[:args.limit]
        print(f"🧪 测试模式: 仅处理前 {len(pending)} 个")

    print()

    # 初始化客户端
    print("🔐 初始化平台客户端...")
    try:
        from alpha_mining.platform.gateway import PlatformGateway

        # 验证环境变量
        username = os.environ.get("WQ_USERNAME", "")
        password = os.environ.get("WQ_PASSWORD", "")

        if not username or not password:
            print("❌ 未找到 WQ_USERNAME 或 WQ_PASSWORD 环境变量")
            return 1

        # 使用PlatformGateway（内部会自动创建ReadOnlyPlatformClient）
        gateway = PlatformGateway(
            state_path=_ROOT / ".wq_auth_state.json",
            database=_ROOT / "research_memory.sqlite",
            lock_path=_ROOT / "worldquant_api.lock",
            min_interval=args.interval,
        )

        # 认证
        print("🔑 执行认证...")
        gateway.authenticate()
        print("✅ 认证成功\n")

    except Exception as exc:
        print(f"❌ 客户端初始化失败: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    # 批量模拟
    print(f"{'=' * 70}")
    print(f"🚀 开始批量Simulate")
    print(f"{'=' * 70}\n")

    success_count = 0
    error_count = 0
    start_time = time.time()

    try:
        for i, candidate in enumerate(pending, 1):
            result = simulate_one(gateway, candidate, i + args.start, len(candidates))
            append_result(output_path, result)

            if result["alpha_id"]:
                success_count += 1
            else:
                error_count += 1

            # 速率控制
            if i < len(pending):
                print(f"  ⏳ 等待 {args.interval} 秒...")
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n\n⏹️  用户中断（Ctrl+C）")
    except Exception as exc:
        print(f"\n\n❌ 批量执行失败: {exc}")
        import traceback
        traceback.print_exc()

    # 统计
    elapsed = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(f"📊 执行完成")
    print(f"{'=' * 70}")
    print(f"总计: {len(pending)} 个候选")
    print(f"成功: {success_count} 个")
    print(f"失败: {error_count} 个")
    print(f"耗时: {elapsed:.1f} 秒")
    print(f"结果文件: {output_path.absolute()}")
    print(f"{'=' * 70}\n")

    # 如果是测试模式且成功，询问是否继续
    if not args.full and success_count > 0 and len(candidates) > len(pending):
        remaining = len(candidates) - len(pending) - args.start
        print(f"💡 测试成功！还有 {remaining} 个候选待处理")
        print(f"   继续执行: python 批量simulate验证.py --full --start {args.start + len(pending)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
