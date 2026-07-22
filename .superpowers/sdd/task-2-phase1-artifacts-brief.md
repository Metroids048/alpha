# Task 2: Phase 1 audit artifacts

Extend the existing offline acceptance/audit generator so one command produces the exact seven approved Phase 1 artifacts in an output directory:

- `DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md`
- `DESCRIPTION_PIPELINE_IMPLEMENTATION.md`
- `ALPHA_QUALITY_CORRELATION_FIX.md`
- `description_backfill_dry_run.csv`
- `description_validation_failures.csv`
- `historical_alpha_eligibility.csv`
- `new_alpha_failure_funnel.csv`

Requirements:

- Reuse `alpha_mining.audit.acceptance.run_acceptance_audit`; do not duplicate its data extraction.
- When there is no fresh COMPLETE non-zero platform Ledger, reports and eligibility/backfill rows must say `BLOCKED`/`PLATFORM_LEDGER_NOT_COMPLETE`; never treat legacy CSV as authoritative platform data.
- CSV files must always exist with stable headers, even with zero data rows.
- `description_backfill_dry_run.csv` contains only `SUBMIT_READY_EXCEPT_DESCRIPTION` jobs and `endpoint_calls=0`.
- `description_validation_failures.csv` includes persisted failed/schema-unknown jobs and reasons.
- `historical_alpha_eligibility.csv` is derived from current ledger + eligibility snapshots/jobs and remains empty/BLOCKED without an authoritative ledger.
- Markdown reports must explicitly state PATCH endpoint calls=0 and Submit endpoint calls=0 for Phase 1.
- Preserve existing acceptance artifacts and behavior.
- Add focused tests first and observe RED.
- Do not create branches/worktrees/commits or call the platform.

Ownership:

- `alpha_mining/audit/acceptance.py` and/or a new helper under `alpha_mining/audit/`
- new `tests/test_phase1_description_artifacts.py`

The worktree is shared and dirty. Do not revert other edits.
