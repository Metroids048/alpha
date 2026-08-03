# Alpha 冻结工作流 v1.0

唯一工作流为：

`生成Alpha.py` → `待提交Alpha列表.csv` → `提交Alpha.py`

`生成Alpha.py` 只执行受 catalog、知识引用和质量门槛约束的生成/模拟闭环；它最多生成 3 个初始候选、每周期最多模拟 12 个、并发固定为 1，允许零输出。只有已有 `alpha_id` 且质量状态为 `READY_TO_SUBMIT` 的记录会原子写入 `待提交Alpha列表.csv`。

`提交Alpha.py` 不重新 simulate。它仅消费 READY CSV，并保留台账同步、Description、SubmissionGuard、dry-run、确认短语与幂等提交保护。

主要目录：

- `alpha_mining/`：领域逻辑、factory、质量与平台适配。
- `tests/`：冻结工作流与历史回归测试。
- `World quant/`：仅 Markdown 知识资料。
- `docs/`、`文档/历史资料/`：文档与历史资料。
- `tools/`：离线工具。

本地 SQLite、认证状态、浏览器配置、Cookie 和运行数据均保持 Git 忽略，不进入代码历史。
