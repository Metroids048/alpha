# FastPlus 接入说明（本仓库）

依赖：`py-fastplus`（Python ≥ 3.12）

入口：`alpha_mining.parser.fastplus_gate.check_expression`

挂接位置：
- `auto_alpha_pipeline_rebuilt_v50.PreflightValidator.validate`（主生成链路）
- `alpha_mining.domain.validation.PreflightValidator`
- `alpha_mining.generation.validation.LocalExpressionValidator`

行为：可用时硬拒绝非法表达式；未安装时 soft-fallback 到原有启发式校验。
