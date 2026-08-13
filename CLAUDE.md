@AGENTS.md

# Claude Code additions

- Use `/context` when instruction loading is uncertain.
- Use project Skills for repeatable multi-step procedures instead of expanding this file.
- For substantial changes, delegate final review to the `code-reviewer` subagent in a fresh context.
- Treat CLAUDE.md and auto memory as guidance, not enforcement. Mandatory checks must be implemented through tests, CI, permissions, or Hooks.
- Auto memory may store stable project discoveries such as real commands, architecture decisions, recurring failure patterns, and proven debugging insights. It must not store secrets, temporary task details, unverified guesses, or user-sensitive information.

<!-- AGENT-CONFIG-PACK:CLAUDE-ADDITIONS START -->
# Claude Code additions (agent-config-pack)

> Installed/updated 2026-07-22.

- Use `/context` when instruction loading is uncertain.
- Use project Skills for repeatable multi-step procedures instead of expanding this file.
- For substantial changes, delegate final review to the `code-reviewer` subagent in a fresh context.
- Treat CLAUDE.md and auto memory as guidance, not enforcement. Mandatory checks must be implemented through tests, CI, permissions, or Hooks.
- Auto memory may store stable project discoveries only. No secrets / temp task / unverified guesses.
- Prefer `verify-work` before claiming COMPLETE.
- **Agent autonomy**: Except for physical actions (face scan, hardware operations), the agent MUST execute commands, read results, and complete verification autonomously. DO NOT ask the user to run commands, paste outputs, or fetch file contents. The agent has full file/command access and should use it.
<!-- AGENT-CONFIG-PACK:CLAUDE-ADDITIONS END -->

<!-- AI-KNOWLEDGE-MANAGED-START -->

@AGENTS.md
@项目知识库/项目总览.md
@项目知识库/当前状态.md
@项目知识库/目标与验收标准.md

复杂任务完成后执行会话归档与项目知识同步。不得将临时任务、猜测、秘密和未验证完成写入长期记忆。

<!-- AI-KNOWLEDGE-MANAGED-END -->
