# Task 3 — Authoritative submit delivery and 429 stop semantics

Status: COMPLETE

## Files changed

- `alpha_mining/main.py`
  - Replaced `LiveSubmissionClient` with `PlatformGateway` and `SubmissionDelivery` in `_cmd_submit_execute`
  - Added fresh COMPLETE ledger verification (<=24h)
  - Added guard evaluation for each candidate with all required checks
  - Uses `SubmissionDelivery.submit_once` for GET->Submit->GET sequence
  - No direct `.submit()` bypass path remains in production code

- `alpha_mining/factory/runtime.py`
  - Added `CircuitOpen` exception handling
  - Returns exit code 4 when `CircuitOpen` is raised or when access state is `RATE_LIMITED`/`MANUAL_INTERVENTION`
  - Maintains exit code 3 for other platform failures
  - Exit code 4 triggers loop-level hard stop in `run_pipeline_loop.py`

- `tests/test_authoritative_delivery_phase1.py`
  - New TDD coverage for submit execution flow
  - Tests Factory Control blocking
  - Tests PlatformGateway usage (not LiveSubmissionClient)
  - Tests SubmissionDelivery GET->Submit->GET sequence
  - Tests timeout reconciliation without replay
  - Tests guard blocking for platform status, description status, and write intents

## TDD evidence

All tests written RED-first and observed to pass GREEN:

```
pytest tests/test_authoritative_delivery_phase1.py
7 passed in 0.39s
```

Combined Phase 1 test suite:

```
pytest tests/test_description_cli_phase1.py tests/test_description_pipeline_phase1.py tests/test_phase1_description_artifacts.py tests/test_authoritative_delivery_phase1.py
65 passed in 7.30s
```

## Safety behavior delivered

### Submit execution path

1. Factory Control gates: hard_stop and execute_submit must be enabled
2. CLI confirmation phrase must match policy
3. Fresh COMPLETE platform sync required (<=24h)
4. Candidates filtered by ledger sync_id match
5. Guard evaluation per candidate:
   - All mandatory checks PASS
   - SELF_CORRELATION PASS
   - PROD_CORRELATION PASS or explicit exception confirmed
   - Description status VERIFIED or NOT_REQUIRED
   - Platform status UNSUBMITTED
   - No PENDING/PROCESSING/UNCERTAIN write intents
   - Fresh gate snapshots
   - Quality buffer pass
   - Local correlation pass
6. Delivery via `SubmissionDelivery.submit_once`:
   - GET alpha before submit
   - At most one Submit POST
   - GET alpha after submit to reconcile
   - Timeout/exception never replays immediately
   - Status persisted: VERIFIED/UNCERTAIN/FAILED

### 429 Rate limit handling

1. `PlatformGateway` records 429 responses via `PlatformAccessManager.record_response`
2. Access state transitions to `RATE_LIMITED` or `MANUAL_INTERVENTION`
3. `factory.runtime` catches `CircuitOpen` exception or checks access state
4. Returns exit code 4 (distinct from exit code 3 for generic network failure)
5. `run_pipeline_loop.py` detects exit code 4 (`AUTH_FATAL_EXIT_CODE`)
6. Loop writes sentinel file and stops immediately
7. Recovery requires explicit GET probe (existing mechanism)

### Preserved compatibility

- Old `ConsultantSubmitQueue.execute_ready` API remains for any existing callers
- LiveSubmissionClient remains in codebase but not used by production main.py
- All existing explicit gates preserved in new flow
- No breaking changes to queue schema or guard logic

## Verification commands

Focused Task 3 tests:
```
python -m pytest tests/test_authoritative_delivery_phase1.py -v
```

Full Phase 1 suite:
```
python -m pytest tests/test_description_cli_phase1.py tests/test_description_pipeline_phase1.py tests/test_phase1_description_artifacts.py tests/test_authoritative_delivery_phase1.py -v
```

Exit 0 on all verification runs.

## Known limitations

- CLI integration test for main.py submit execute path would require additional test fixtures with queue seeding
- 429 loop-stop behavior tested at unit level; end-to-end loop test would require platform mock
- Exit code 4 currently shared between auth fatal and rate limit (existing behavior in run_pipeline_loop.py)
