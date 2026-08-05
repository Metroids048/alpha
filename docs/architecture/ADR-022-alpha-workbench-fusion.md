# ADR-022: Alpha 主链与桌面工作台融合

## 状态

Accepted

## Decision

保留 `alpha系统` 的 PyQt 外壳和研究展示能力，但所有候选队列、模拟、质量判定、描述准备和提交确认都通过 `alpha_mining.factory.operator_service.CandidateWorkflowService`。SQLite `数据/本地运行产物/数据库/research_memory.sqlite` 是唯一工作流主账；`待提交Alpha列表.csv` 只作为启动导入和单向投影。

平台写入继续由 `PlatformGateway`、`DescriptionDelivery`、`SubmissionDelivery` 和 `FactoryControl` 负责。未知结果只能通过 checkpoint 或 GET 对账恢复；桌面层不再使用第二套 `BrainClient`、动态 `exec()` 评分或无限重试提交器作为主链。

## Consequences

- 生成器仍可在无界面、无认证环境下纯本地运行。
- GUI 依赖通过 `requirements-gui.txt` 隔离。
- 根目录旧数据库不删除；双库工作流数据非空时启动迁移会拒绝。
- PC Range、旧 EXE、教程素材和标签/Osmosis 不属于首版工作流主链。
