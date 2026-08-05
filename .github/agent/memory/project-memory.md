# Alpha Project Memory

Last updated: 2026-08-05

## Current verified state

- `[VERIFIED]` Current repository is `C:\Users\Windows11\Desktop\alpha`, branch `main`, HEAD `fb8e883`; the working tree was clean before this memory-only change. Source: `git status --short --branch`, `git log -1`.
- `[VERIFIED]` The active `生成Alpha.py` workflow is offline-only: it reads local field/dataset caches, does not authenticate, simulate, or submit. Platform session and catalog access are prerequisites only for later catalog refresh, simulation, and submission. Source: `项目知识库\当前状态.md`, updated 2026-08-04.
- `[VERIFIED]` The current platform boundary is blocked by an expired/invalid WorldQuant session; current docs require a fresh legal session and read-only catalog sync before simulate/submit. Source: `项目知识库\当前状态.md`, `docs\02-frozen-solution-v2.1.md`, `World quant\核心：API认证问题及解决方案.md`.
- `[VERIFIED]` Current acceptance/audit artifacts report `Submit endpoint calls: 0`, `Fully eligible real candidates: 0`, and no PATCH calls while the platform ledger is incomplete. Source: `alpha_mining\audit\acceptance.py`, `alpha_mining\audit\access_recovery.py`.

## Diversity and ladder evidence

- `[VERIFIED]` The legacy v50 configuration contains `explore_batch_quota: 0` and `arch_explore_batch_quota: 0` by default. The `diverse_exploration` preset sets `arch_explore_batch_quota` to 9. This is a legacy/configuration fact, not evidence that the active offline entry produces diverse candidates. Source: `auto_alpha_pipeline_rebuilt_v50.py`, commit `25b526a` (2026-07-20), current tests `tests\test_v50_tuning_policy.py`.
- `[VERIFIED]` `alpha_mining\filter\ladder_check.py` currently requires an explicit `threshold` argument for yearly Sharpe checks and uses `_DEFAULT_FAST_WINDOW = 21` for fast-signal detection; a default yearly threshold of `1.0` is not present in the current file. Source: current file inspection 2026-08-05.
- `[USER_REPORTED]` The historical observation that approximately 92% of candidates were near-duplicate parameter tweaks and that ladder threshold `1.0` remained as a disabled feature is retained as a follow-up claim, but no current artifact in this checkout independently proves the percentage. Do not use it as a verified metric.

## Security and authentication lessons

- `[VERIFIED]` The repository contains a history scanner that rejects tracked session-cookie artifacts such as `.wq_persona_session_cookies.json`, and commit `ccba78d` added the scanner and regression tests. Source: `tools\security\verify_git_history.py`, `tests\test_security_scanner.py`, commit `ccba78d` (2026-08-01).
- `[USER_REPORTED]` A session-cookie file was previously committed to a public repository. The durable rule is: never read, commit, transmit, or paste session cookies; run the history scanner before delivery.
- `[VERIFIED]` The active alpha platform path imports `alpha_mining.auth.session_manager` for shared authentication. Current root operations scripts that need auth are under `tools\ops`; the old concern about root scripts bypassing the manager is retained for historical review, not asserted as a current defect.
- `[VERIFIED]` `alpha_mining\auth\browser_login.py` exists in the current checkout. Any historical agent statement that the file existed without checking the filesystem is an evidence failure. Source: `Test-Path`/current file inspection 2026-08-05.
- `[VERIFIED]` Current authentication documentation describes Persona/Biometric responses and polling, while the live platform session remains unavailable. Source: `World quant\核心：API认证问题及解决方案.md`, `项目知识库\当前状态.md`.

## Operating rule

Do not describe offline candidate generation as successful WorldQuant simulation or submission. Keep `generated`, `simulated`, `READY`, and `submitted` as separate evidence states.
