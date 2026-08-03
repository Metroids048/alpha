# Alpha 冻结工作流 v1.0

唯一工作流为：

`生成Alpha.py` → `待提交Alpha列表.csv` → `提交Alpha.py`

`生成Alpha.py` 默认执行受 catalog、知识引用和质量门槛约束的生成/模拟闭环；它最多生成 3 个初始候选、每周期最多模拟 12 个、并发固定为 1，允许零输出。只有已有 `alpha_id` 且质量状态为 `READY_TO_SUBMIT` 的记录会原子写入 `待提交Alpha列表.csv`。

纯离线候选生成是独立辅助工具，必须显式运行 `python -m alpha_mining.offline.cli`；它不联网、不模拟，也不会写入 `待提交Alpha列表.csv`。离线候选不是可提交 Alpha。

`提交Alpha.py` 不重新 simulate。它仅消费 READY CSV，并保留台账同步、Description、SubmissionGuard、dry-run、确认短语与幂等提交保护。

主要目录：

- `alpha_mining/`：领域逻辑、factory、质量与平台适配。
- `tests/`：冻结工作流与历史回归测试。
- `World quant/`：仅 Markdown 知识资料。
- `docs/`、`文档/历史资料/`：文档与历史资料。
- `tools/`：离线工具；`tools/ops/` 为运维/诊断脚本。
- `auto_alpha_pipeline_rebuilt_v50.py`：测试回归单体（非日常操作入口）。

本地 SQLite、认证状态、浏览器配置、Cookie 和运行数据均保持 Git 忽略，不进入代码历史。运行产物统一放在 `数据/本地运行产物/`（报告 / 状态 / 数据库 / 备份）。

故障恢复：如果入口输出 state=CATALOG_UNAVAILABLE，这不是“生成了 0 个合格 Alpha”，而是平台目录尚未具备生成条件。先完成网页登录并导入新的本地会话，再依次运行：

    python -m alpha_mining platform probe
    python -m alpha_mining platform catalog-sync
    python 生成Alpha.py

生产闭环（需要有效平台会话和完整 catalog）：

    python 生成Alpha.py

只有 datasets、data-fields、operators 三类目录都成功同步，生成入口才会继续；--once 在目录不可用时返回退出码 8，常驻模式会输出 CATALOG_BACKOFF 后等待重试。
