"""Retired direct-auth scan runner; retained only as a fail-closed entry point."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "brain_scan_pipeline.py 已停用：请运行 python 启动Alpha主线.py，"
        "只读平台操作请使用 python -m alpha_mining platform 子命令。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
