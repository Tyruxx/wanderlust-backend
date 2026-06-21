---
name: wanderlust-execution-workflow
description: Use this skill for every Wanderlust Trip development task before editing, testing, committing, or reporting back. It defines the project execution loop: read specs, protect user changes, implement narrowly, verify against specs and guardrails, run security audit, commit before final response, and report remaining risk clearly.
---

# Wanderlust Execution Workflow

Use this skill for every Wanderlust Trip task that changes code, docs,
configuration, prompts, agent workflows, tests, deployment files, or project
state. Pair it with `../wanderlust-agentic-security/SKILL.md` whenever the
task touches agents, tools, backend APIs, external services, secrets,
persistence, deployment, model output, or ACTIVE itinerary behavior.

## Execution Loop

1. **Orient first.**
   - Read the relevant files before editing.
   - Read `../../../specs/README.md` and the specific specs that govern the
     request, especially guardrails in
     `../../../specs/03-product-workflows-and-guardrails.md`.
   - Inspect current git status in the affected repo before edits.
   - Treat uncommitted work as user or prior-agent work; do not revert it
     unless explicitly asked.

2. **Threat-model and scope the change.**
   - Identify affected surfaces: Flutter UI/state, backend API, ADK workflow,
     tools, prompts, external-source ingestion, persistence, deployment,
     secrets, tests, docs, or specs.
   - State the smallest change that satisfies the request.
   - Preserve product guardrails: local-first preferences, saved itineraries,
     one ACTIVE itinerary, ACTIVE-only events, explicit user confirmation for
     sensitive actions, and social sources as discovery-only.

3. **Implement deliberately.**
   - Follow existing architecture and naming.
   - Keep frontend, backend, specs, docs, and tests aligned.
   - Prefer deterministic validation and typed schemas over ad hoc parsing.
   - Do not introduce new external services, permissions, or dependencies
     unless the request needs them and docs/env are updated.

4. **Verify against the task and specs.**
   - Run the narrowest useful tests first, then broader checks when touched
     surfaces justify it.
   - For Flutter changes, normally run `flutter analyze` and relevant
     `flutter test`.
   - For backend changes, normally run `ruff check app tests scripts` and
     `pytest`; use `WANDERLUST_DB=memory` for repository tests when the local
     SQLite file is not writable.
   - Search for stale terminology when product direction changes, such as
     old auth, storage, preference, itinerary, or style naming.
   - Confirm changed behavior still matches `specs/`.

5. **Run the security audit before finalizing.**
   - Load and apply `../wanderlust-agentic-security/SKILL.md` when applicable.
   - Scan changed files for secrets, private keys, copied console output,
     service-account JSON, raw API keys, and sensitive logs.
   - Check that `.env`, `.env.*`, SQLite databases, generated secrets, and
     local credentials remain ignored.
   - Confirm no new agent/tool path can bypass validation, allowlists,
     ACTIVE-only gates, schema checks, source verification, or explicit user
     acceptance.

6. **Commit before reporting back.**
   - Stage only files intentionally changed for the task.
   - Review staged diff and run `git diff --cached --check`.
   - Run a staged secret scan before committing.
   - Commit in every affected git repo before the final response, unless the
     user explicitly says not to commit or a blocker makes committing unsafe.
   - If project files are outside any git repo, report that clearly.

7. **Report with evidence.**
   - Summarize what changed, where, and why.
   - List verification commands and results.
   - Include security audit findings and residual risks.
   - Mention commit hashes and any remaining uncommitted files that predate or
     sit outside the task.

## Pre-Final Checklist

- Specs and guardrails checked.
- Relevant implementation and docs updated together.
- Tests/lints run or inability explained.
- Security audit completed.
- Staged diff reviewed and secret scan clean.
- Commit created in each affected repo.
- Final response includes commit hash, verification, audit result, and
  remaining risk.
