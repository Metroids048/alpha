# Consultant Alpha vNext Implementation Report

Date: 2026-07-20  
Mode: local snapshot, offline-first, no real submission

## Delivered architecture

- Added independent domain modules for normalization, AST parsing, behavior/structure signatures, operator registry, field catalog, and preflight validation.
- Added the Dynamic Gate Registry, local-payload sync, scope-aware snapshots, freshness enforcement, explicit read-only live adapter, and JSON snapshot export.
- Added the streaming Legacy Knowledge Lake importer, sanitized platform/check persistence, canonical deduplication, lineage, deterministic medoids, dynamic-gate triage, and five CSV reports.
- Added Level-1 fingerprints, date-aligned Pearson/Spearman returns comparison, absolute sign-flip risk, insufficient-history status, and normalized returns storage.
- Added bounded Consultant generation, behavior-aware mutation admission, persistent Bandit rewards, OFAT settings optimization, robustness scoring, simulation budgets, quality judge, immutable queue decisions, and fail-closed submission guard.
- Expanded `python -m alpha_mining` with `gates`, `legacy`, `correlation`, `consultant`, and `submit` command groups while preserving prior commands.
- The legacy loop/cycle/supervisor entry files were not changed. The v50 monolith now delegates its core expression identities to the independent domain layer and no longer marks pending self-correlation as a submission candidate.

## Schema migrations

The idempotent version ledger creates or extends:

- `platform_gate_observations`, `platform_gate_snapshots`
- `legacy_alphas`, `alpha_check_events`, `alpha_expression_features`
- `alpha_behavior_clusters`, `alpha_cluster_members`, `alpha_lineage`
- `settings_trials`, `legacy_triage_results`
- `alpha_daily_returns`, `alpha_correlation_results`
- `consultant_bandit_events`, `consultant_submit_queue`

Migrations are additive, transactionally recorded, and explicitly close SQLite handles for Windows compatibility. Tests run against temporary databases and never modify `alpha_mining_smoke.sqlite3`.

## CLI

```text
python -m alpha_mining gates sync [--source CSV] [--live --alpha-id ID]
python -m alpha_mining gates show
python -m alpha_mining legacy import
python -m alpha_mining legacy triage
python -m alpha_mining legacy report
python -m alpha_mining correlation refresh
python -m alpha_mining correlation inspect --expression-id ID
python -m alpha_mining consultant generate
python -m alpha_mining consultant simulate
python -m alpha_mining consultant shadow-run
python -m alpha_mining submit dry-run
python -m alpha_mining submit execute --confirm I_UNDERSTAND_REAL_SUBMISSION
```

`submit execute` additionally requires `consultant.execute_submit: true` in a private config. The committed value is false. Dry-run does not import or instantiate the live submission client.

## Local-data acceptance results

- Gate sync: 16,304 logical rows scanned, 129,841 observations recorded, 30 snapshots exported.
- Latest usable observation time in the local data: `2026-07-03T13:25:52Z`; therefore all snapshots are stale under the configured 24-hour submission TTL on 2026-07-20.
- Legacy import: 18,659 source records scanned, 13,325 canonical records, 5,305 exact duplicates, 18,630 lineage rows, and 129,841 check events.
- Behavior clustering: 8,739 clusters and deterministic medoids.
- Triage: RECHECK 477, REPAIR 439, SEED_ONLY 12,121, ARCHIVE 288.
- Default top research seeds (RECHECK/SEED_ONLY medoids): 8,247.
- Reports: inventory 18,630; cluster summary 8,739; seed candidates 12,598; repair candidates 439; archive 288.
- Submission dry-run: 477 evaluated, 0 allowed, 477 blocked, 0 endpoint calls.
- Dominant block reasons: stale/missing Gate snapshot 477, insufficient local returns history 477, self-correlation pending 460, self-correlation missing 17, low sub-universe failure 108.

## Test results

- Pre-change baseline: `429 passed, 5 subtests passed`.
- Consultant behavior suite: `16 passed`.
- Final full-suite verification: `445 passed, 5 subtests passed in 57.24s`.
- `python -m compileall -q alpha_mining` passed.
- Static architecture check passed: no `alpha_mining` source contains an old-monolith dependency string.
- Migration against a temporary copy of `alpha_mining_smoke.sqlite3` created all Consultant table families and left the checked-in sample hash unchanged.

## Credential handling

- `.wq_browser_cookie.json` is now ignored and new code never logs or persists its plaintext value.
- A DPAPI migration helper was added. It writes the protected state first, performs one read-only alpha-detail GET, and moves the plaintext file only after HTTP 200 verification.
- Automated migration was not attempted without a configured username and a caller-selected alpha ID; the existing cookie file and automation session were therefore left untouched.
- The exposed Cookie should be rotated. Rewriting public Git history remains outside this local refactor.

## Known limitations

- Local raw API payloads are stale, so no candidate can pass the submission freshness gate until an explicit authenticated `gates sync --live --alpha-id ...` is performed.
- Historical source data contains no sufficient normalized daily-return history for current RECHECK candidates; correlation refresh must populate at least 60 aligned observations before submission.
- Large-cluster medoids use deterministic CLARA sampling above 500 members rather than quadratic exact PAM.
- The workspace has no `.git`; logical checkpoint boundaries were followed, but no commits were created or claimed.
- Legacy fallback thresholds remain isolated in the preserved v50/loop path. Consultant submission decisions never consume them as platform truth.

## Next shadow run

```text
python -m alpha_mining gates sync --live --alpha-id <known-alpha-id>
python -m alpha_mining consultant shadow-run --hypothesis-id <id> --family fundamental --field <field>
python -m alpha_mining consultant simulate --family fundamental --quality-score 0.7 --metric-ratio 0.9
python -m alpha_mining submit dry-run
```

Do not enable `consultant.execute_submit` until Gate snapshots are fresh, returns correlations are populated, all platform checks explicitly PASS, and the dry-run reports zero blockers.
