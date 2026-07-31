"""Retired direct-auth batch runner; retained only as a fail-closed entry point."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "brain_batch_resim.py 已停用：请运行 python 启动Alpha主线.py，"
        "平台访问必须经过集中认证和 PlatformGateway。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
