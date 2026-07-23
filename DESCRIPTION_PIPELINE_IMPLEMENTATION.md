# Description Pipeline Implementation

## Phase 1 safety boundary

- Status: **PASS**
- Ledger authority: `be3f8a22124ddf09f7b95f6a`; rows: `10000`.
- Blocker: `none`.
- PATCH endpoint calls: 0
- Submit endpoint calls: 0

The Phase 1 output is offline-only: it records validated backfill candidates without requesting a platform PATCH or submission.

## Core pipeline functions

- Fact extraction: `extract_description_facts`
- Description generation: `build_deterministic_description`
- Validation: `validate_description`
- Status tracking: `DescriptionStatus`
- Job persistence: `description_backfill_jobs`
- Delivery: `DescriptionDelivery.patch_once` (sequence: `GET -> PATCH -> GET`)

## CLI commands

- `inspect`: View ledger state
- `generate`: Rebuild description from persisted facts
- `dry-run`: Preview backfill candidates
- `backfill`: Execute description patch
- `resume`: Check job status
