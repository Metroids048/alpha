"""Command-line interface for the network-free candidate generator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .metadata import MetadataCacheError, MetadataCacheMissing, MetadataCacheStale
from .service import OfflineCandidatePoolExhausted, run_offline_generation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从本地平台缓存生成并校验 Alpha 候选 CSV")
    parser.add_argument("--cache-dir", type=Path, default=Path("数据/平台缓存"))
    parser.add_argument("--queue-path", type=Path, default=Path("数据/候选队列/候选Alpha.csv"))
    parser.add_argument("--events-path", type=Path, default=Path("数据/候选队列/处理事件.csv"))
    parser.add_argument("--count", type=int, default=100, help="队列目标候选总数")
    parser.add_argument("--cache-max-age-hours", type=float, default=168)
    parser.add_argument("--allow-stale-cache", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        print(f"{exc}。请先运行 python 同步平台元数据.py。", file=sys.stderr)
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
