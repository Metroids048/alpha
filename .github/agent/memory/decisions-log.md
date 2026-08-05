# Alpha Decisions Log

## 2026-08-05 — Preserve fail-closed platform boundary

- **Context:** The current offline entry can generate from local metadata, while WorldQuant catalog refresh, simulation, and submission require a valid platform session.
- **Decision:** Treat the offline entry and platform delivery chain as separate states. Never infer simulate/submit success from local candidate generation or a nonzero queue.
- **Consequences:** Memory and mirror summaries must report the current platform blocker and the exact `0` submit evidence. No credential bypass, freshness fabrication, or automatic submit retry is allowed.
- **Evidence:** `项目知识库\当前状态.md`; `docs\02-frozen-solution-v2.1.md`; `alpha_mining\audit\acceptance.py`; `alpha_mining\audit\access_recovery.py`.

## 2026-08-05 — Treat diversity quota values as configuration evidence only

- **Context:** Legacy v50 contains `explore_batch_quota=0` and `arch_explore_batch_quota=0`, while a named diversity preset sets the architectural quota to 9.
- **Decision:** Record the values and their scope, but do not claim a current 92% collapse rate without a reproducible cohort report.
- **Consequences:** Future changes must include a cohort distribution artifact before promoting a diversity claim to `[VERIFIED]`.
- **Evidence:** `auto_alpha_pipeline_rebuilt_v50.py`; `tests\test_v50_tuning_policy.py`; commit `25b526a`.

## 2026-08-05 — Cookie and filesystem-claim guard

- **Context:** The repository now contains a history scanner for session-cookie paths, and the current checkout does contain `alpha_mining\auth\browser_login.py`.
- **Decision:** Run the scanner and a filesystem existence check before recording security or file-existence claims. Historical agent output without a command result is not evidence.
- **Consequences:** Unsupported claims remain `[USER_REPORTED]` and do not drive implementation decisions.
- **Evidence:** `tools\security\verify_git_history.py`; `tests\test_security_scanner.py`; current file inspection.
