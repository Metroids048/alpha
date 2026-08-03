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

### 2026-08-03 — 生产入口可调用不等于平台链路已验收

- Repeated failure: 入口代码能启动，但连续输出 generated=0 / CATALOG_UNAVAILABLE，现场仍被描述为“链路已打通”。
- Root cause: catalog 门禁缺少 operators 快照，且平台访问状态为 RATE_LIMITED、保存会话 stale。
- Evidence: 生成Alpha.py --once 返回退出码 8；platform access-status 显示 RATE_LIMITED；.alpha_operators_cache.json 不存在。
- New rule or automation: 交付前同时核对入口执行结果、platform access-status 和三份 catalog 缓存；默认常驻模式遇 catalog 阻塞只做受控 backoff，不伪造生成成功。
- Scope: alpha 生成入口 / 平台恢复流程
- Review date: 下一次完成真实只读 probe + catalog-sync 后
