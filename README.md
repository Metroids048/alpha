# Alpha 冻结工作流 v1.0

唯一工作流为：

`生成Alpha.py` → `待提交Alpha列表.csv` → `提交Alpha.py`

`生成Alpha.py` 是纯 Alpha 生产入口：每轮只读取本地 catalog、历史反馈和 `World quant/` Markdown，调用配置的 DeepSeek 做研究/生成/批判，再将少量通过本地硬门槛的候选原子写入 `待提交Alpha列表.csv`。它不打开浏览器、不登录、不访问 World Quant 平台、不 simulate、不生成 `alpha_id`，允许零输出。

`待提交Alpha列表.csv` 在该阶段表示**待平台 simulate 队列**。新行固定为 `queue_status=PENDING_SIMULATION`、`alpha_id` 为空、`degraded=false`、`knowledge_usage_mode=LIVE_LLM_KNOWLEDGE`。`local_quality_score` 只是本地排序分，不是 Sharpe、Fitness 或平台通过保证。

旧 `alpha_mining.factory.runtime`、`alpha_mining.offline.cli` 仍保留为回归/辅助工具，但不再是 `生成Alpha.py` 的活动链路，也不会作为 LLM 失败后的静默降级。

`提交Alpha.py` 的浏览器登录、批量 simulate、平台结果解析和反馈回写行为保持不变；本阶段不改造提交链路。平台通过率必须由下一阶段真实 simulate 验证。

主要目录：

- `alpha_mining/`：领域逻辑、factory、质量与平台适配。
- `tests/`：冻结工作流与历史回归测试。
- `World quant/`：仅 Markdown 知识资料。
- `docs/`、`文档/历史资料/`：文档与历史资料。
- `tools/`：离线工具；`tools/ops/` 为运维/诊断脚本。
- `auto_alpha_pipeline_rebuilt_v50.py`：测试回归单体（非日常操作入口）。

本地 SQLite、认证状态、浏览器配置、Cookie 和运行数据均保持 Git 忽略，不进入代码历史。运行产物统一放在 `数据/本地运行产物/`（报告 / 状态 / 数据库 / 备份）。

故障恢复：如果入口输出 state=CATALOG_UNAVAILABLE，这不是“生成了 0 个合格 Alpha”，而是本地完整目录尚未具备生成条件。目录同步是独立的运维动作；生成入口本身不会联网。完成同步后再运行：

    python -m alpha_mining platform probe
    python -m alpha_mining platform catalog-sync
    python 生成Alpha.py

生产生成（需要完整本地 catalog 与 DeepSeek 配置）：

    python 生成Alpha.py

只有 datasets、data-fields、operators 三类目录都可从本地快照读取，生成入口才会继续；`--once` 在目录不可用时返回退出码 8，常驻模式记录 `CATALOG_UNAVAILABLE` 后等待重试。
