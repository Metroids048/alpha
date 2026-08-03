# Alpha Generation Correction Baseline

## Scope

The active generation path is `生成Alpha.py` to
`alpha_mining.factory.runtime` to the preserved v50 candidate-only boundary,
then catalog screening, `FactoryOrchestrator.execute_candidate`, quality
classification, feedback, and `待提交Alpha列表.csv`. Generation does not submit.

## Test Contract Changes

| Test area | Old assertion | Why it was incorrect | Replacement coverage |
| --- | --- | --- | --- |
| Authoritative pipeline | `FactoryOrchestrator` accepted and called `CandidateGenerationService` | The approved runtime removes this parallel authority. The orchestrator now owns only request lifecycle execution. | Asserts that the orchestrator exposes `execute_candidate` and has no `candidate_service` constructor injection. |
| Generation entry | `QualityAlphaWorkflow`, `CandidateGenerationService`, and the four-Chinese-JSON loader were active | These paths are explicitly retired by the frozen architecture. | Asserts the entry/runtime use the factory runtime and reject the retired dependencies. |
| Platform errors | Unrecognised failures were expected to become `LOW_SHARPE` | The frozen rule requires fail-closed `UNKNOWN`; guessing would trigger an invalid repair. | Preserves `UNKNOWN` and verifies it is not overwritten. |
| Mandatory metric checks | Metric-style checks were excluded from check failure handling | A platform `mandatory: true` `LOW_*` or turnover failure must be as blocking as every other mandatory hard check. | Verifies a mandatory `LOW_SHARPE=FAIL` blocks READY and the active cycle writes neither CSV nor submit request. |
| Production correlation naming | Only `PROD_CORRELATION` was recognised | The submission guard also accepts the platform's `PRODUCTION_CORRELATION` spelling. | Canonicalizes the alias and verifies it satisfies the required correlation gate. |
| SQLite fixtures | Minimal rows omitted current non-null schema columns | `INSERT OR IGNORE` silently skipped these rows, producing misleading zero statistics. | Fixtures supply `research_topics` and `hypotheses` required columns, then verify real `simulation_runs` aggregate into `topic_stats`. |
| Ready CSV | Partial rows were treated as the current queue schema | The active store uses a fixed, complete ready-row contract. | Tests populate the required queue fields and assert rows are normalized by the store. |
| v50-to-factory bridge | No executable bridge contract existed | Static source scans cannot prove a candidate reaches the active lifecycle without submission. | Verifies stable adapter identity/dataset and an active fake cycle with one simulation, zero submissions, and no READY CSV after a mandatory failure. |

## Verification Evidence

On 2026-08-03, the following completed with exit code 0:

```powershell
& $env:AGENT_PYTHON -m compileall -q alpha_mining tests '生成Alpha.py' '提交Alpha.py'
& $env:AGENT_PYTHON -m pytest -q
```

The full suite completed without failures. Live Brain simulation and submission
were not run; no claim is made about live signal quality or platform access.
