"""唯一中文生产入口：转发到受控 Alpha 主线 supervisor。"""

from run_pipeline_supervisor import main


if __name__ == "__main__":
    raise SystemExit(main())
