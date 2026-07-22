# Task 1: Description CLI

Add the public `python -m alpha_mining description ...` command family required by the approved Phase 1 plan.

Required subcommands:

- `inspect --alpha-id ID`
- `generate --alpha-id ID`
- `validate --alpha-id ID`
- `dry-run --alpha-id ID`
- `patch --alpha-id ID`
- `verify --alpha-id ID`
- `backfill --dry-run`
- `backfill --execute`
- `resume --job-id ID`

Binding safety rules:

- Unknown/missing ledger rows or schemas fail closed with exit code 2 and a non-secret JSON/text reason.
- Read-only commands use the latest local platform ledger observation and never create a platform client.
- `patch` requires all of: Factory Control permits Description PATCH, exact confirmation phrase `I_UNDERSTAND_PLATFORM_WRITES`, and a VALIDATED persisted job. It must use `PlatformGateway` plus `DescriptionDelivery` so the network sequence is GET -> at most one PATCH -> GET.
- `verify` is read-only and may GET only when explicitly invoked; it must never PATCH.
- `backfill --dry-run` never creates a platform client and reports only eligibility/validation work.
- `backfill --execute` means PATCH/verify only; it does not submit. It requires the same persisted write enable and confirmation phrase.
- `resume` is idempotent and fail closed for unknown or uncertain jobs.
- No command may call Submit.
- Keep current dirty main changes; do not revert unrelated edits, do not create branches, do not commit.

Implementation ownership:

- `alpha_mining/main.py`
- new files under `alpha_mining/description/` needed for CLI/service behavior
- new `tests/test_description_cli_phase1.py`

Use TDD: add focused tests and observe the missing-command failure before implementation. Run the focused test file when complete. Do not touch `factory/orchestrator.py` or existing tests.
