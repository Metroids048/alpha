# Agent lessons

Record only recurring, verified lessons that should change future work. Keep each entry concise and periodically delete obsolete items.

## Template

### YYYY-MM-DD — <short lesson>

- Repeated failure: <what happened at least twice>
- Root cause: <verified cause>
- Evidence: <test, log, file, command>
- New rule or automation: <specific prevention>
- Scope: <global project / path / workflow>
- Review date: <date or triggering condition>

### 2026-08-04 — 离线入口不得被完整平台 catalog 门禁阻塞

- Repeated failure: 后续生成链融合把缺少 `.alpha_operators_cache.json` 视为 `生成Alpha.py` 的硬阻塞，覆盖了既有完全离线运行约定。
- Root cause: `5d04cca` 的纯本地入口沿用了完整 catalog 加载器，未启用已有的字段/数据集离线缓存回退。
- Evidence: 修复前 `生成Alpha.py --once` 返回 8；修复后真实 `--once` 以 5697 本地字段和 15 个内置算子完成，退出码 0，未访问 WorldQuant；全量 pytest 通过。
- New rule or automation: 离线入口优先完整本地快照，缺少 operators 时必须回退到本地字段/数据集缓存和内置语法；平台会话只属于刷新、simulate 和提交路径。
- Scope: alpha 生成入口 / 本地 catalog 加载
- Review date: 下次调整 catalog 加载策略时
