# Testing and verification

- Read the verification matrix in `AGENTS.md` before deciding which checks apply.
- For bug fixes, reproduce the failure and add a regression test when feasible.
- Do not modify valid tests merely to make them pass.
- Run targeted tests after each meaningful change and mandatory broader checks before completion.
- Execute changed behavior in a realistic environment when feasible; do not rely only on static inspection.
- Record exact commands and outcomes. A skipped or unavailable check is unverified.
- Stop repeated repair after the limits in `AGENTS.md` and report evidence rather than looping indefinitely.
