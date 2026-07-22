# Task 3: Authoritative submit delivery and 429 stop semantics

Replace the remaining public live submit bypass and make 429 a loop-level hard pause.

Requirements:

1. `python -m alpha_mining submit execute` must use `PlatformGateway`, `SubmissionGuard`, fresh queue/Ledger evidence, and `SubmissionDelivery`. It must no longer use `LiveSubmissionClient` or a direct `.submit()` path.
2. Preserve all existing explicit gates: FactoryControl hard stop off, `execute_submit` enabled, CLI `--execute-submit`, policy enable, exact confirmation, current COMPLETE <=24h Ledger with matching sync, mandatory/base checks PASS, SELF PASS, PROD PASS or explicit exception, Description `VERIFIED/NOT_REQUIRED`, platform still `UNSUBMITTED`, no PENDING/PROCESSING/UNCERTAIN write intent.
3. The write sequence for an allowed candidate is GET -> at most one Submit -> GET. Timeout/exception never replays immediately; it reconciles with GET and persists VERIFIED/UNCERTAIN/FAILED via `SubmissionDelivery`.
4. Keep old queue APIs for compatibility if tests require them, but production `main.py` must not call `LiveSubmissionClient` or direct submission.
5. `PlatformGateway` remains the only production network gateway for submit.
6. When any platform request records HTTP 429 and the persistent access state becomes `RATE_LIMITED` or `MANUAL_INTERVENTION`, `alpha_mining.factory.runtime` must return a dedicated exit code distinct from generic network failure. `run_pipeline_loop.py` must persist a `rate_limit_circuit_open` failure, write its sentinel, stop the loop, and never auto-retry the cycle. Recovery stays the existing explicit GET probe path.
7. 401/403 remain fail-closed and bounded; do not add authentication retry loops.
8. Add TDD tests with fake gateways/clients only; no real platform calls. Test submit GET->POST->GET, timeout reconciliation/no replay, public main path not using `LiveSubmissionClient`, guard blockers, runtime 429 exit mapping, and outer loop hard-stop mapping.
9. Shared dirty main, preserve others, no branch/worktree/commit.

Ownership:

- `alpha_mining/main.py` submit handler only
- `alpha_mining/submitter/queue.py`, `alpha_mining/submitter/delivery.py`, and a new executor helper if needed
- `alpha_mining/factory/runtime.py`
- `run_pipeline_loop.py`
- focused new tests, preferably `tests/test_authoritative_delivery_phase1.py`
