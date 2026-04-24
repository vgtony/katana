# AGENTS.md

## Scope

Single-agent coding only.
Environment: Antigravity IDE with ChatGPT 5.4.
Repository type: backend-only Flask API/service.

## Core Rules

- Make the smallest correct change.
- Preserve existing architecture, naming, and patterns.
- Fix root causes, not symptoms.
- Do not refactor unrelated code.
- Do not add dependencies unless required.
- Do not hardcode secrets, credentials, hosts, ports, or environment-specific values.
- Do not claim success without verification.
- If verification was not run, say so explicitly.

## Workflow

- Read the relevant code before editing.
- Follow existing repo conventions.
- Keep diffs small and reviewable.
- Update only code, tests, and docs affected by the task.
- Do not leave dead code, misleading comments, or vague TODOs.

## Flask / API Rules

- Keep handlers thin.
- Preserve existing Blueprint/app structure.
- Keep validation near the request boundary.
- Preserve API contracts unless change is explicitly requested.
- Do not silently change status codes, response fields, payload shape, or error behavior.
- Return explicit failures; do not mask backend errors.

## Integration Rules

This repo may talk to OpenStack, OpenNebula, MongoDB, Kafka, Kubernetes, SSH targets, Proxmox, and external HTTP services.

When changing integration code:

- preserve existing auth and connection patterns,
- preserve expected side effects,
- handle remote failures explicitly,
- do not assume external systems are available,
- call out risk when local verification is impossible.

## Runtime Rules

- Treat Gunicorn/runtime behavior as production-sensitive.
- Do not change startup, binding, workers, or deployment behavior unless required.
- Distinguish code changed on disk from code active in a running process.
- If restart/redeploy/reload is required, state it explicitly.

## Verification

Run the smallest meaningful verification first.
Prefer:

- targeted tests,
- narrow local runs,
- broader checks only when needed.

In the final response, state:

- what changed,
- files changed,
- commands run,
- commands not run,
- open risks.

Do not present static inspection as runtime verification.

## Version Control

Use Git and GitHub.

- Check changes with `git status` and `git diff`.
- Keep commits focused.
- Do not bundle unrelated changes.
- Do not rewrite shared history unless explicitly asked.

## Escalation

If small patches keep failing:

- stop repeating the same approach,
- name the failing assumption,
- switch to a scoped refactor,
- explain why.

## Avoid

- broad rewrites,
- speculative cleanup,
- silent contract changes,
- hidden fallbacks,
- unverifiable claims,
- unnecessary process overhead.
