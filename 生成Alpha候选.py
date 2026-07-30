"""用户入口：仅转发到离线候选生成 CLI。"""

from alpha_mining.offline.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
