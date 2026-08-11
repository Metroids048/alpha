# PROCESS_DRIFT_ROOT_CAUSE

Status: `EXTERNAL_VALIDATION_BLOCKED`

## Baseline

- Branch: `main`
- HEAD: `9a3c3fe2c5e4598f187c8e820f9475d5d7c5787f`
- Worktree: clean before the temporary harness; no tracked files changed.
- Current entry: `生成Alpha.py -> alpha_mining.generation.production.main`.
- Preserved factory path: `v50_adapter.generate_candidates -> adapt_v50_candidate -> FactoryOrchestrator.execute_candidate -> PlatformGateway.simulate`.

## Evidence and current blocker

- The fixed-settings evidence contains four complete platform results: `78zwRNqx` (Sharpe `0.03`, Fitness `0.00`), `e73OZE9z` (`-0.88`, `-0.36`), `2rp7V1wb` (`0.25`, `0.10`), and `ak1d5dlv` (`0.26`, `0.03`).
- The `tmp_simulate_report.json` row at `truncation=0.05` remains excluded.
- The four B rows are accepted from the original platform JSON (`alpha_id`, `COMPLETE`, full settings, metrics, checks/status fields). Missing duplicate local database provenance is recorded as `FEEDBACK_PERSISTENCE_EVIDENCE_GAP`, not as an A/B blocker.
- The validation queue maps all four B rows to explicit `parent_seed` values by exact candidate-id prefix and exact rewritten expression.
- A simulation did not start because preserved v50 candidate generation hit platform data-set requests returning 429 and then 401; bounded re-login was unavailable because `WQ_USERNAME/WQ_PASSWORD` are not configured. No A result is inferred from this failure.

## Drift timeline

1. `c1b4bc1` (2026-08-01) explicitly diagnosed four generators bypassing v50 and restored the v50 ExpressionFactory/FieldCatalog/NearPassAmplifier path.
2. `0f20a7b` introduced `QualityAlphaWorkflow` as an authoritative generation architecture.
3. `874209d9` restored `factory.runtime` and changed the entrypoint test to protect that intermediate architecture.
4. `99a4ae2` changed the entrypoint test to require `generation.production` and forbid `factory.runtime`.
5. `514217d` changed `生成Alpha.py` to `generation.production`, where `V50Kernel` supplies seeds and `HighQualityGenerator` performs LLM research and expression rewriting.
6. `generation_chain_final_acceptance.md` treated local legality, knowledge grounding, and `local_quality_score` as implementation evidence while explicitly stating that platform Sharpe/Fitness had not been proved.

## Root causes

- v50's role drifted from preserved generator to seed provider without an architecture-equivalence A/B.
- Local engineering proxies replaced platform Sharpe/Fitness as the effective acceptance signal.
- Static entrypoint tests encoded an intermediate architecture and had no traceability requirement to business behavior.
- “Implementation complete” froze architecture before fresh external validation.

## Required process gates

- `Behavior Baseline Lock`: commit, real entry, behavior contract, and platform KPI evidence are recorded before migration.
- `Architecture Equivalence Gate`: entry, scheduler, generator, writer, state machine, data source, or core algorithm changes require same-settings real A/B.
- `Acceptance Criteria Immutability`: every architecture assertion in tests must trace to user intent or a verified historical baseline; otherwise label `SUSPECT_TEST_CONTRACT`.
- `Business KPI Stop Rule`: two repair cycles without median Sharpe, Fitness, or NEAR_PASS/PASS improvement force `RETURN_TO_ROOT_CAUSE_DIAGNOSIS` and prohibit another gate patch.

`Root Cause Frozen` does not imply `Architecture Frozen`. Code implementation and external validation remain separate phases.

## PROCESS_DRIFT_008_HARD_GATE_WITHOUT_BUSINESS_NECESSITY

A blocking acceptance condition is justified only when failure would make the core business conclusion impossible. Arbitrary sample counts, duplicate evidence in a second local store, document completeness, or evidence-file formatting are not business hard gates by themselves. If an authoritative external platform result already verifies the fact under the fixed contract, a second local persistence record is not required unless the task is specifically testing that persistence system. The earlier requirement for exactly five B rows and duplicate `PLATFORM_VERIFIED` database provenance failed this necessity test and correctly had to be removed from the A/B contract.
