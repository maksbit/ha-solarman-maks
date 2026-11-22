# AGENTS.md — ha-solarman-maks

Read the existing repository documentation, relevant source and tests, current branch, open changes and applicable safety rules before editing. This file supplements, not replaces, existing project guidance.

Preserve upstream attribution and license. Work in this owner-controlled fork only. Do not modify live inverter settings, issue control writes, deploy to Home Assistant or submit upstream changes without separate authorization.

## Ruflo Orchestration Policy

- Ruflo is optional for development and must not be invoked for routine single-agent tasks. Use it only for explicitly owner-authorized multi-agent work.
- Maximum two concurrent executors in total, including the primary Codex agent; no nested swarms or recursive agent spawning. One review cycle per implementation stage; stop at the approved scope and report blockers instead of unbounded retries.
- Use a version-pinned, reviewed Ruflo installation with a minimal MCP tool allowlist. Do not initialize project hooks, daemons, federation or additional services implicitly. Ruflo agent registration is not evidence of independent execution or review.
- No autonomous production deployment, live equipment actions, privilege escalation, or bypass of approval/security gates. All existing repository rules, more restrictive limits and data-access policies take precedence.
- Repository documentation and versioned Git history remain the authoritative project memory. Ruflo memory is supplementary only; never store credentials, tokens, sensitive telemetry, private records or other restricted data in it, and do not transmit repository data to external services without authorization.
- Record actual validation, decisions, remaining blockers and the next step in the existing project handoff workflow; do not claim tests or reviews that were not performed.
