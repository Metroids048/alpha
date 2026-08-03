"""Command-line interface for the network-free candidate generator."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from .metadata import MetadataCacheError, MetadataCacheMissing, MetadataCacheStale
from .service import OfflineCandidatePoolExhausted, run_offline_generation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从本地平台缓存生成并校验 Alpha 候选 CSV")
    parser.add_argument("--cache-dir", type=Path, default=Path("."))
    parser.add_argument("--queue-path", type=Path, default=Path("数据/候选队列/候选Alpha.csv"))
    parser.add_argument("--events-path", type=Path, default=Path("数据/候选队列/处理事件.csv"))
    parser.add_argument("--count", type=int, default=100, help="队列目标候选总数")
    parser.add_argument("--cache-max-age-hours", type=float, default=168)
    parser.add_argument("--allow-stale-cache", action="store_true")
    parser.add_argument("--loop", action="store_true", help="常驻运行离线候选生成")
    parser.add_argument("--interval", type=float, default=30.0, help="循环间隔秒数")
    parser.add_argument("--batch-size", type=int, default=10, help="每轮新增候选目标数")
    parser.add_argument("--max-rounds", type=int, default=0, help="仅用于受控运行；0 表示持续运行")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.loop:
        return _run_loop(args)
    return _run_once(args)


def _run_once(args: argparse.Namespace) -> int:
    try:
        summary = run_offline_generation(
            cache_dir=args.cache_dir,
            queue_path=args.queue_path,
            events_path=args.events_path,
            count=args.count,
            cache_max_age_hours=args.cache_max_age_hours,
            allow_stale_cache=args.allow_stale_cache,
        )
    except MetadataCacheMissing as exc:
        print(
            f"{exc}。离线模式不会自动联网；请提供完整本地元数据快照，"
            "或在完成网页登录后显式运行 python 生成Alpha.py --production。",
            file=sys.stderr,
        )
        return 2
    except MetadataCacheStale as exc:
        print(f"{exc}。确认后可加 --allow-stale-cache 继续。", file=sys.stderr)
        return 2
    except MetadataCacheError as exc:
        print(f"平台元数据缓存无效: {exc}", file=sys.stderr)
        return 2
    except OfflineCandidatePoolExhausted as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(
        f"离线生成完成: 新增 {summary.added}，已有 {summary.existing}，"
        f"本地拒绝 {summary.rejected}，队列 {summary.queue_path}"
    )
    return 0


def _run_loop(args: argparse.Namespace) -> int:
    rounds = 0
    last_code = 0
    try:
        while args.max_rounds <= 0 or rounds < args.max_rounds:
            args.count = max(
                int(args.count),
                _queue_count(args.queue_path) + max(1, int(args.batch_size)),
            )
            last_code = _run_once(args)
            rounds += 1
            if args.max_rounds > 0 and rounds >= args.max_rounds:
                return last_code
            print(
                f"离线循环第 {rounds} 轮完成；{max(1.0, float(args.interval)):g} 秒后继续。",
                flush=True,
            )
            time.sleep(max(1.0, float(args.interval)))
    except KeyboardInterrupt:
        return 0
    return last_code


def _queue_count(queue_path: Path) -> int:
    if not queue_path.is_file():
        return 0
    with queue_path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(main())
