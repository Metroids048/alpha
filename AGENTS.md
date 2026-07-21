# Repository Instructions

## Git branch policy (hard)

- Only use branch `main`. Do not create, checkout, push, or rename any other branch (including `codex/*`).
- Do not use Git worktrees that create a new named branch. Work in the existing local checkout on `main`.
- Do not change the repository default branch away from `main`.
- If you need an isolated sandbox, stay in detached HEAD / local-only worktree and never `git push -u origin HEAD` or `git push origin <new-branch>`.

Before making a substantive plan or code change in this repository, search the local `skills/**/SKILL.md` files. If one or more skills clearly match the current task, read the matching skill files and follow their workflow before continuing with the normal implementation flow.