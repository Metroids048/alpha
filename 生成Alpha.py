"""纯 Alpha 生产入口：只生成待平台 simulate 的候选，不模拟或提交。"""

from __future__ import annotations

from alpha_mining.generation.production import main


if __name__ == "__main__":
    raise SystemExit(main())
