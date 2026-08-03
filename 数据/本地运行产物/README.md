# 本地运行产物

根目录散落的 CSV / JSON / SQLite 等运行与报告文件收口于此（通常已被 `.gitignore` 忽略）。

- `报告/` — shadow_run / new_alpha / submission / platform / legacy 等导出
- `状态/` — pipeline_*_state、gate_snapshot、novelty 等
- `数据库/` — 当前研究库（如 `research_memory.sqlite`）
- `备份/` — `*.backup*`、test-artifact、smoke 库

冻结入口默认数据库：`数据/本地运行产物/数据库/research_memory.sqlite`。
