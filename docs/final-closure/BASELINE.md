# Final Closure Baseline

**Established:** 2026-08-01
**Branch:** main
**HEAD:** ac6068511e43560096f6652477faeb75d0aa1381
**Remote:** https://github.com/Metroids048/alpha (public)
**Working tree:** clean
**Test baseline:** 718 passed, 5 subtests passed

---

## 1. Production Call Chain (verified)

```
启动Alpha主线.py
  → from run_pipeline_supervisor import main
    → subprocess: python run_pipeline_cycle.py
      → from alpha_mining.factory.runtime import main
        → FactoryOrchestrator(database, simulation)
          → ConsultantGenerator (directly instantiated in __init__)
          → SimulationRequestStore
          → ResearchArmTracker.record_window()
```

**v50 status:** `auto_alpha_pipeline_rebuilt_v50.py` appears only in comments in production files.
No production import or delegation to v50. CONFIRMED NOT in production path.

---

## 2. Static Analysis Answers

| Question | Answer |
|---|---|
| `启动Alpha主线.py` final module | `alpha_mining.factory.runtime` (via run_pipeline_supervisor subprocess) |
| `run_pipeline_cycle.py` only calls factory.runtime | YES |
| `factory.runtime` instantiates FactoryOrchestrator | YES |
| Production imports v50 | NO (comments only) |
| FactoryOrchestrator candidate source | `ConsultantGenerator` directly in `__init__` |
| `生成Alpha候选.py` CSV consumed by production loop | NO — production loop uses SQLite only |
| ResearchArmTracker/EvolutionEngine/IdeaGenerator in prod generation | NO — not connected to FactoryOrchestrator |

---

## 3. Problem Baseline (frozen)

### P0-01 — Git History Contains Sensitive Authentication Material
- **Status:** CONFIRMED — 4555 sensitive path hits across 9 unique commits
- **Commits:** 0588733d, 1e13b5c1, 273a170e, 5a7b30a7, 6a2ac6cb, 84caa428, c7603012, dc3c53dd, e093f2d9
- **Types:** `.wq_browser_profile/` (browser profile with potential session data)
- **.gitignore:** Rules are present for these paths
- **Action:** BLOCKED_BY_USER_AUTHORIZATION (WORLDQUANT_SESSION_REVOCATION_CONFIRMED=false, ALLOW_GIT_HISTORY_REWRITE_AND_FORCE_PUSH=false)
- **Non-blocked deliverable:** `tools/security/verify_git_history.py` scanner script + CI gate

### P1-01 — Candidate Generation and Feedback Chain Not Unified
- **Status:** CONFIRMED
- `FactoryOrchestrator` directly owns `ConsultantGenerator` — no injection point
- No `CandidateGenerationService` interface
- `IdeaGenerator`, `EvolutionEngine` exist but not wired into production generation
- `ResearchArmTracker.record_window()` requires full batch of 20+ observations; no `record_observation()`
- `SimulationRequestStore.claim()` has no `context` parameter — candidate context is lost on restart
- `RequestLease` has no `context` field
- No `CandidateFeedbackStore` / `candidate_outcomes` table
- Fallback on restart uses `PENDING_BACKLOG` / `UNKNOWN` placeholder with no real context
- Migration version: 16 (next: 17)

### P2-R1 — Playwright Not Declared in requirements.txt
- **Status:** CONFIRMED
- `alpha_mining/auth/browser_login.py` uses Playwright
- Not in `requirements.txt`, `requirements-llm.txt`, `requirements-test.txt`
- No `requirements-browser.txt` exists
- **Action:** Add `requirements-browser.txt`

---

## 4. Explicitly Out of Scope

- Rewriting v50 / legacy monolith cleanup
- Rewriting PlatformGateway / submission guard
- Relaxing self/prod correlation gate
- Real Alpha submission
- Full auth architecture rewrite
- Renaming/reformatting unrelated code

---

## 5. Planned Changes

| File | Action |
|---|---|
| `tests/test_authoritative_candidate_pipeline.py` | NEW — 11 failing tests |
| `alpha_mining/generation/screening.py` | NEW — CandidateScreeningPolicy |
| `alpha_mining/generation/feedback.py` | NEW — CandidateFeedbackStore |
| `alpha_mining/generation/service.py` | NEW — CandidateGenerationService |
| `alpha_mining/generation/__init__.py` | UPDATE — export new types |
| `alpha_mining/factory/simulation_requests.py` | UPDATE — add context_json |
| `alpha_mining/storage/migrations.py` | UPDATE — migration 17 |
| `alpha_mining/factory/orchestrator.py` | UPDATE — inject candidate_service |
| `alpha_mining/scheduler/arm_metrics.py` | UPDATE — add record_observation() |
| `tools/security/verify_git_history.py` | NEW — P0 scanner |
| `tests/test_security_scanner.py` | NEW — scanner unit tests |
| `.github/workflows/test.yml` | UPDATE — CI security gate |
| `requirements-browser.txt` | NEW — Playwright optional dep |
| `tests/test_playwright_requirements.py` | NEW — static dep declaration test |

---

## 6. Verification Command

```bash
python -m pytest -q
```

Pass condition: >= 718 tests pass (plus new tests green).
