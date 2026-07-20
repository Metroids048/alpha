# Consultant Alpha vNext Repository Audit

Date: 2026-07-20  
Workspace: `C:/Users/win/Desktop/alpha`

## Executive findings

- The real production entry chain is `run_pipeline_supervisor.py` → `run_pipeline_loop.py` → `run_pipeline_cycle.py` → `auto_alpha_pipeline_rebuilt_v50.py`.
- The preserved outer loop is simulate-only unless submit draining is explicitly requested. Real submission additionally requires `--execute-submit`.
- The workspace is a source snapshot without `.git`; no local branch or commit history can be asserted. The public GitHub repository shows `codex/v50.4-pipeline-recovery` as its selected branch.
- Baseline before the Consultant implementation was `429 passed, 5 subtests passed`.
- `总alpha.csv` has 281,070 physical text lines but 16,304 logical CSV records because embedded JSON/error text contains newlines. It still must be streamed rather than loaded as one list.
- A plaintext browser-cookie file was present in the public source tree. Its value was not printed or copied during this audit.

## Entrypoints and current CLI

The supervisor starts the loop as a subprocess, shares the DPAPI-protected auth-state path, preserves authentication/network exit codes, and restarts only on non-zero exits. The loop launches `run_pipeline_cycle.py`, which optionally applies the resilient async patch and delegates to the v50 monolith.

Existing package commands before this refactor were:

- `install-topics`
- `run-evolution`
- `backfill`
- `observe-feedback`
- `pipeline`

The Consultant commands are deliberately additive and are not called by the supervisor or loop.

## Reverse dependencies on the legacy monolith

Before extraction:

- `alpha_mining/generator/data_mapping.py` imported `FieldCatalog` and field-quality helpers.
- `alpha_mining/storage/backfill_from_csv.py` imported normalization, structure signature, and a platform-pass proxy.
- `alpha_mining/generator/expression.py` lazily imported normalization and structure helpers.
- `alpha_mining/main.py` imported the monolith to run its CLI.
- `alpha_mining/simulate/async_batch.py` accepted the monolith instance as a callback surface and invoked private simulation/check helpers. This remains a legacy compatibility adapter and is not used by the Consultant path.

After extraction, package domain modules do not import the monolith. The top-level cycle entry remains the isolated compatibility boundary.

## Hard-coded platform-like thresholds

The legacy path contains historic defaults for Sharpe, Fitness, minimum/maximum turnover, queue similarity, ladder Sharpe, near-pass ranking, and local self-correlation. Important locations include `PipelineConfig`, `_metric_gate_pass_proxy`, `queue_decision`, recheck priority, `queue_probe.py`, `analysis/legacy_triage.py`, and loop queue arguments.

These values are retained only for the preserved fallback behavior. The Consultant path never uses them as platform truth: it reads limits from versioned Gate Registry snapshots and applies separately configured safety margins.

## PENDING, MISSING, and UNKNOWN paths

- The legacy check poller correctly retains pending self-correlation rows for bounded recheck.
- The old feedback analysis previously marked `SELF_CORRELATION=PENDING` as a `submission_candidate`; this was tightened to require explicit `PASS` while preserving recheck scheduling.
- Legacy `queue_probe.py` only rejected explicit `FAIL` and did not independently require a self-correlation PASS. It remains isolated behind the old fallback; Consultant submit decisions use `SubmissionGuard` and fail closed.
- Missing check arrays, timeouts, unknown check results, missing Gate snapshots, and insufficient returns history all block Consultant submission.

## Settings-only and coefficient-only variants

- The legacy mutation engine can vary windows, normalization, neutralization, and composite coefficients without proving behavioral novelty.
- Historical generators contain sign inversion, coefficient blends, adjacent windows, and settings retries that are useful as negative examples but are not Consultant research ideas.
- Consultant behavior signatures deliberately collapse whole-expression sign, scalar coefficients, rank-centering constants, and simulation settings. Its mutation policy rejects parameter-only changes.

## Credential and privacy risks

- `.wq_browser_cookie.json` contains authentication-like fields and was visible in the public repository listing.
- `.wq_auth_state.json` is already ignored and uses current-user DPAPI protection.
- Logs, CSV payload fields, notebooks, legacy scripts, and fixtures were scanned for credential-related keys. New importers recursively redact Authorization, Cookie, password, token, API-key, username, and email keys before persistence.
- Cookie rotation and remote Git-history rewriting are operational actions outside this source refactor. Plaintext quarantine is allowed only after DPAPI import and a successful read-only platform verification.

## SQLite inventory and migration risks

The checked-in `alpha_mining_smoke.sqlite3` contains `alpha_payloads`, `simulations`, `metrics`, `checks`, and `submit_queue`. The Research Memory code independently creates topics, hypotheses, mappings, expressions, simulations, returns, observations, repairs, and topic statistics.

Migration risks:

- the two schema families may coexist in one database;
- sample databases must never be modified in place during tests;
- Windows holds SQLite files open if connections are not explicitly closed;
- JSON payload columns can be malformed, truncated, or use inconsistent casing;
- duplicate expressions can have multiple alpha IDs and conflicting settings/check history;
- missing platform timestamps must not become fresh Gate observations merely because they were imported today.

The new migration runner uses additive `CREATE TABLE/INDEX IF NOT EXISTS`, a version ledger, explicit connection closure, and no destructive DDL.

## Protected compatibility contract

- No Consultant command is invoked automatically by the loop or supervisor.
- `execute_submit` remains false in committed configuration.
- Dry-run does not construct or call a real submission endpoint.
- Network/auth exit codes, loop state/sentinel behavior, existing queue files, and legacy simulation callbacks remain available.
- No historical CSV is rewritten and no bulk historical resimulation is started.
