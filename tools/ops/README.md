# tools/ops

根目录一次性/运维诊断脚本收口目录。

冻结业务入口仍在仓库根：`生成Alpha.py`、`提交Alpha.py`、`验证提交链路.py`。
`auto_alpha_pipeline_rebuilt_v50.py` 因测试回归依赖保留在根目录。

认证探测 CLI：`python tools/ops/wq_auth_check.py`（原根目录 `test_wq_auth.py`，已改名避免被 pytest 收集）。
