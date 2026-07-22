---
name: verify-work
description: Use after implementing, fixing, refactoring, migrating, or reviewing code to verify the result against the original request with real tests, execution evidence, an independent diff review, retry limits, and an honest completion report. Do not use for pure brainstorming or explanation-only tasks.
---

# Verify work

Verify the current task using evidence outside the implementation's own reasoning.

## Inputs to gather

1. Original user request or approved task specification.
2. Applicable `AGENTS.md`, `CLAUDE.md`, Cursor rules, and project documentation.
3. Current git diff and relevant surrounding code.
4. Repository-defined test, lint, type-check, build, integration, end-to-end, security, and domain validation commands.
5. Existing failure logs and prior repair attempts.

## Phase 1: Build an acceptance map

Create a compact mapping:

| Acceptance criterion | Evidence required | Current status |
|---|---|---|

Do not invent criteria or commands. Mark missing evidence as `UNKNOWN`.

## Phase 2: Deterministic verification

Run the narrowest relevant checks first, then all mandatory broader checks for the affected area.

For every check capture:

- Exact command or procedure.
- Exit status.
- Important output.
- `PASS`, `FAIL`, `BLOCKED`, or `NOT_RUN`.

A check is never `PASS` based only on code inspection or model confidence.

## Phase 3: Behavioral verification

Exercise the changed user/system path in a realistic environment when feasible. Include meaningful boundary, error, empty, retry, timeout, state-recovery, compatibility, or concurrency cases relevant to the change.

For UI work, inspect the actual rendered behavior rather than only source code. For integrations, compare intended and actual requests/responses without exposing secrets. For data or trading systems, use sandbox, historical, paper, or dry-run evidence unless real execution is explicitly authorized.

## Phase 4: Test-integrity check

Inspect whether implementation changed tests, fixtures, snapshots, mocks, thresholds, lint rules, or validation configuration.

Fail verification if a valid guard was weakened only to obtain a passing result. A changed test is acceptable only when the approved requirement changed or the old test is demonstrably wrong; document the evidence.

## Phase 5: Independent review

Use a fresh context or a read-only reviewer/subagent when available. Give it:

- Original requirements.
- Applicable project rules.
- Final diff.
- Real verification output.

The reviewer must not edit files. Every finding needs a precise location, violated criterion, evidence, and impact. Deterministic failures override reviewer opinion.

## Phase 6: Repair loop

When a check fails:

1. Classify the failure: implementation defect, test defect, environment block, requirement conflict, or human judgment.
2. Feed the exact failure evidence back into the repair step.
3. Make the smallest targeted change.
4. Re-run the failed check and relevant regressions.
5. Increment the attempt counter.

Limits:

- Maximum 3 automatic repairs for one failing check.
- If the same check fails twice without meaningful new evidence or progress, stop repeating the approach and reassess.
- Stop immediately for conflicting requirements, unavailable mandatory data/environment, unsafe or irreversible action, production/real-money action without authorization, or a decision requiring human product/risk judgment.

## Final output

Return:

- Verdict: `COMPLETE`, `PARTIAL`, or `BLOCKED`.
- Acceptance criteria and evidence table.
- What changed and why.
- Exact commands/procedures run and results.
- Independent review verdict and unresolved findings.
- Skipped/unavailable checks and why.
- Known limitations and residual risks.
- Repair attempts made.
- Smallest next action if not complete.

Never claim completion when a mandatory criterion lacks passing evidence.
