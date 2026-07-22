# Task 1 — Description CLI report

Status: DONE

## Files changed

- `alpha_mining/main.py` — registers `python -m alpha_mining description` and all required Phase 1 subcommands.
- `alpha_mining/description/cli.py` — local-only, fail-closed command service.
- `tests/test_description_cli_phase1.py` — parser, local-ledger inspect, and default PATCH safety coverage.

## TDD and verification evidence

- RED command: `& $env:AGENT_PYTHON -m pytest -q tests/test_description_cli_phase1.py`
  - Before implementation: `9 failed`; every failure was `invalid choice: 'description'` from the missing command family.
- Final focused command: `& $env:AGENT_PYTHON -m pytest -q tests/test_description_cli_phase1.py`
  - Output: `11 passed in 0.48s` (exit 0).
- Diff whitespace check: `git diff --check -- alpha_mining/main.py alpha_mining/description/cli.py tests/test_description_cli_phase1.py`
  - Exit 0; Git emitted only its LF-to-CRLF working-copy warning for `alpha_mining/main.py`.

## Safety behavior delivered

- All required subcommands parse: inspect, generate, validate, dry-run, patch, verify, backfill (`--dry-run` / `--execute`), and resume.
- Commands consult the existing SQLite ledger read-only; unknown ledger rows or description schemas return a non-secret JSON reason and exit code 2.
- Default patch/backfill-execute paths require the exact confirmation phrase and Factory Control permission, then remain blocked because a durable description payload is not yet persisted.
- No default path constructs a platform client; verify is explicitly local-only and never PATCHes.

## Concerns

Initial concern (resolved below): the first narrowed implementation was a safe skeleton because its job schema lacked durable payload/fact evidence.

## Review remediation (Phase 1 completion)

The initial skeleton concerns above have been resolved.

- Migration 10 durably persists canonical description payload JSON, extracted facts JSON, and validation errors JSON on `description_backfill_jobs`; `DescriptionPipeline.prepare()` now writes those artifacts.
- The CLI verifies its local evidence by joining `platform_alpha_ledger` to `platform_sync_runs`: the run must be `COMPLETE`, have nonzero reconciled counts, retain the matching sync ID, and be no older than 24 hours.
- `generate` rebuilds from persisted facts/schema, while `validate` re-runs the real validator on persisted payload/facts/schema and persists validation errors.
- A dependency-injected gateway factory enables deterministic fake-gateway tests. Production construction remains lazy and is reached only after the ledger, confirmation, Factory Control, and validated-job checks pass.
- `patch` delegates to `DescriptionDelivery` (GET → at most one PATCH → GET), `verify` performs one explicit read-only GET, `backfill --execute` invokes only description patch/verification work, and `resume` is a no-write idempotent status report for stable jobs while unknown/uncertain jobs fail closed.

Final verification command:

```powershell
& $env:AGENT_PYTHON -m pytest -q tests/test_description_cli_phase1.py tests/test_description_pipeline_phase1.py
```

Output: `48 passed in 4.22s` (exit 0).

Final diff check:

```powershell
git diff --check -- alpha_mining/main.py alpha_mining/description/cli.py alpha_mining/description/pipeline.py alpha_mining/storage/migrations.py tests/test_description_cli_phase1.py .superpowers/sdd/task-1-description-cli-report.md
```

Exit 0; only existing Git LF-to-CRLF working-copy warnings were emitted for `alpha_mining/main.py` and `alpha_mining/storage/migrations.py`.

## Backfill review remediation

- RED command: `& $env:AGENT_PYTHON -m pytest -q tests/test_description_cli_phase1.py`
  - Before the fix: `2 failed, 18 passed`. The failures showed that historical jobs for the same alpha were counted alongside the authoritative ledger sync, and a failed child patch emitted its own JSON before the final backfill summary.
- `backfill` now selects `(alpha_id, sync_id, job_id)` by joining validated jobs to `platform_alpha_ledger` on both alpha and current sync. It validates that exact tuple again before processing, so historical jobs are deterministically excluded and a current job cannot be processed twice.
- The batch loop no longer uses `alpha_ids.index`. Per-item patch failures are silent when `emit=False`; execute mode emits exactly one aggregate JSON document with accurate `candidates`, `patched`, and `blocked` totals.
- GREEN command: `& $env:AGENT_PYTHON -m pytest -q tests/test_description_cli_phase1.py`
  - Output: `20 passed in 1.53s` (exit 0).
