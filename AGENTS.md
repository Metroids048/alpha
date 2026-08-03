# Project Agent Guide — alpha

> Shared SSOT for Cursor / Codex / Claude Code. Tool-specific patches live under `.cursor/rules/` and `CLAUDE.md`.
> Pack installed 2026-07-22 from `agent-config-pack`. Global Working Agreement applies from user globals.

## 1. Project purpose

- Primary goal: WorldQuant Brain alpha mining / consultant factory pipeline — generate, validate, describe, and submit alphas under platform constraints.
- Primary users: local operator (research + automated pipeline).
- Current priority: description pipeline / factory runtime / platform access recovery (see `.superpowers/sdd/`).
- Explicit non-goals: inventing platform APIs; weakening submission guards; real-money / production deploy without explicit approval.

## 2. Sources of truth

1. Approved task brief / SDD report under `.superpowers/sdd/` or user message
2. Executable tests under `tests/`
3. Current implementation under `alpha_mining/`
4. Audit / acceptance docs (`CONSULTANT_*.md`) when they do not conflict with tests
5. Older notes / CSV dumps — informational only

## 3. Repository map

- Application entry: frozen root scripts `生成Alpha.py` → `待提交Alpha列表.csv` → `提交Alpha.py`; package CLI `python -m alpha_mining`; regression monolith `auto_alpha_pipeline_rebuilt_v50.py` (tests only, not operator UX)
- Ops diagnostics: `tools/ops/` (check/diagnose/fix/setup helpers)
- Core domain: `alpha_mining/` (generator, factory, description, platform, submitter, scheduler, storage)
- Tests: `tests/`
- Config / state: local sqlite/json/csv artifacts; `.env` / cookies are sensitive
- Generated / local data: `*.csv`, `*.sqlite*`, `alpha_state.json`, cookie quarantine — do not treat as source of truth for code design
- High-risk: `alpha_mining/platform/`, `alpha_mining/submitter/`, `alpha_mining/auth/`, real Brain submission paths

## 4. Environment and commands

- Runtime: Python 3 (prefer `$env:AGENT_PYTHON` / project venv when present)
- Targeted test: `& $env:AGENT_PYTHON -m pytest -q tests/<test_file>.py`
- Broader suite: `& $env:AGENT_PYTHON -m pytest -q`
- Before claiming global Python broken: `& $env:AGENT_PYTHON "$env:USERPROFILE\.ai-workspace\scripts\resolve-test-runner.py"`
- Do not invent npm/verify-all commands for this repo.

## 5. Architecture boundaries

- Platform I/O goes through `alpha_mining/platform/` adapters; do not scatter raw Brain HTTP calls.
- Submission / guard logic stays in `submitter` + related guards; do not bypass for convenience.
- Description pipeline owns description generation/validation; keep facts vs delivery separation.
- Prefer extending existing modules over new top-level packages.

## 6. Coding conventions

- Follow nearby Python patterns; minimal diff; no drive-by refactors.
- Validate external/platform input at boundaries; do not swallow failures into false success.
- Tests must not hit live `*.worldquantbrain.com` (socket guard in pytest).

## 7. Required workflow

### Analysis-only

Do not edit files when the user asks only for analysis, planning, review, research, or explanation.

### Implementation

1. Restate observable acceptance criteria.
2. Inspect relevant code, tests, config, and git diff.
3. Non-trivial work: short milestones with verification each.
4. Smallest coherent change; run targeted pytest after milestones.
5. Substantial changes: independent read-only review when available (`verify-work` / code-reviewer).

### Bug fixes

Reproduce → root cause → regression test when feasible → minimal fix → re-verify.

## 8. Verification matrix

| Check | Mandatory when | Command / evidence | Pass condition |
|---|---|---|---|
| Targeted tests | Any behavior change | `& $env:AGENT_PYTHON -m pytest -q tests/<file>.py` | Exit 0 |
| Related suite | Cross-module change | focused multi-file pytest | Exit 0 |
| Full tests | Release / broad risk | `& $env:AGENT_PYTHON -m pytest -q` | Exit 0 / known skips documented |
| Domain / live Brain | Only with explicit user auth | dry-run / sandbox evidence | No unauthorized live submit |

Never report an unexecuted check as passed.

## 9. Test integrity

- Do not delete, skip, weaken, or rewrite valid tests to fit implementation.
- Do not disable network/socket guards to make tests "pass".

## 10. Safety and protected operations

Never without explicit user authorization:

- Live Brain submission / real-money related actions
- Production deploy or irreversible DB/storage wipes
- Force-push / rewrite shared git history
- Reading/committing/transmitting secrets, `.env`, or session cookies
- Disabling submission guards, corr gates, or audit controls

## 11. Retry and escalation

- Max 3 automatic repairs per failing check.
- Same failure twice without progress → stop, reassess, escalate with evidence.

## 12. Completion format

Status: `COMPLETE` | `PARTIAL` | `BLOCKED` — plus changed files, exact commands/results, skipped checks, residual risks.

## 13. Durable project memory

Use `docs/AGENT_LESSONS.md` for recurring verified lessons only (not task chatter).

## Git branch policy (hard)

- Only use branch `main`. Do not create, checkout, push, or rename any other branch (including `codex/*`).
- Do not use Git worktrees that create a new named branch. Work in the existing local checkout on `main`.
- Do not change the repository default branch away from `main`.
- If you need an isolated sandbox, stay in detached HEAD / local-only worktree and never `git push -u origin HEAD` or `git push origin <new-branch>`.

## Local skills

Before making a substantive plan or code change in this repository, search the local `skills/**/SKILL.md` files. If one or more skills clearly match the current task, read the matching skill files and follow their workflow before continuing with the normal implementation flow.

<!-- AGENT-CONFIG-PACK:PROJECT-BRIDGE START -->
## Agent Config Pack bridge (2026-07-22)

Shared cross-tool contract for this repo (Cursor / Codex / Claude Code):

- Global Working Agreement lives in user globals (`~/.codex/AGENTS.md`, `~/.claude/AGENTS.md`, Cursor `00-agent-working-agreement.mdc`).
- This file (`AGENTS.md`) is the **project SSOT**. Claude imports it via `@AGENTS.md` in `CLAUDE.md`.
- Tool patches: `.cursor/rules/00-core-workflow.mdc`, `.cursor/rules/10-verification.mdc`, `.claude/rules/testing.md`.
- Before claiming COMPLETE: use `verify-work` skill (global or project `.agents/.cursor/.claude/skills/verify-work`).
- Analysis / planning / review-only requests: do not edit files.
- Max 3 auto-repairs per failing check; same failure twice without progress → stop and escalate with evidence.
- Never report unexecuted checks as passed. Prefer project-documented verify commands.
- Durable lessons only in `docs/AGENT_LESSONS.md` (no secrets, no temp task chatter).
- Substantial changes: independent read-only review via `.claude/agents/code-reviewer` when available.
<!-- AGENT-CONFIG-PACK:PROJECT-BRIDGE END -->

<!-- AI-KNOWLEDGE-MANAGED-START -->

## 共享用户记忆与项目知识

开始涉及本项目的非简单任务前，读取：

- `项目知识库/项目总览.md`
- `项目知识库/当前状态.md`
- `项目知识库/目标与验收标准.md`
- `项目知识库/开放事项.md`

默认收口模式；任务结束生成会话记录并调用中央同步；不得直接重写中央主知识库。

<!-- AI-KNOWLEDGE-MANAGED-END -->
