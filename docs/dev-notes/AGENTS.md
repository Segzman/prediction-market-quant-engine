# OpenCode Workspace Guide

This repository is configured for OpenCode with `azure/gpt-5.4-pro` as the main working model.

## Default Workflow

- Default to shipping working changes, not just plans.
- Use `Plan` mode for ambiguous, risky, or high-scope work. Use `Build` mode for normal implementation.
- Keep progress updates short and only send them when they add useful information.
- Persist through the full task when feasible: inspect, implement, verify, and report outcomes in one pass.

## Tooling Preferences

- Prefer OpenCode tools over shell equivalents whenever a dedicated tool exists.
- Prefer `glob` and `grep` for file discovery and content search.
- Read related files in parallel when the needed set is already known.
- Prefer patch-based edits for manual code changes.
- Use subagents deliberately:
  - `@explore` for broad read-only codebase discovery.
  - `@general` for parallel research or multi-step work.
  - `@review` for code-review passes focused on bugs and regressions.

## Editing Rules

- Match existing patterns before introducing new abstractions.
- Prefer the smallest correct change.
- Keep behavior safe by default; do not silently change user-visible behavior without reason.
- Do not revert or overwrite user changes unless explicitly asked.
- If concurrent edits conflict with the current task, stop and ask.
- Avoid destructive commands such as hard resets or force pushes unless explicitly requested.
- Do not commit or push unless the user asks.

## Verification

- Run the smallest relevant verification for the change you made.
- Prefer targeted tests before broad suites unless the task warrants full verification.
- If verification is skipped because it is slow, flaky, credentialed, or unrelated, say so explicitly.

## Review Standard

- For review requests, lead with findings.
- Prioritize correctness issues, regressions, security risks, and test gaps.
- Include clickable file references whenever possible.
- Keep any overall summary brief and secondary.

## Repo Noise To Ignore

Ignore these unless the task is specifically about runtime artifacts or operations data:

- `.web_runtime/`
- `.venv/`
- `__pycache__/`
- `*.jsonl`
- `runtime_status.json`
- `alerts.jsonl`
- `source_copy/`
- `source_copy.zip`

Treat `.env` files and credentials as sensitive. Do not read, print, or modify them unless the user explicitly asks.

## GPT-5.4 Pro Biases

- Be concise by default.
- Avoid long upfront plans unless the user asked for one.
- Make reasonable assumptions and proceed when the missing detail is non-critical.
- For hard debugging or architectural work, spend effort on the root cause rather than patching symptoms.
- When multiple reads or searches are needed, batch them instead of stepping through the codebase one file at a time.
