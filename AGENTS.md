# Backend Agent Instructions

Before doing Wanderlust Trip development work, load and follow:

- `skills/wanderlust-execution-workflow/SKILL.md`

Before changing backend agent workflows, ADK tools, prompts, MCP/tool integrations,
external-source ingestion, ACTIVE itinerary event handling, persistence,
deployment, model-output handling, or API key handling, also load and follow:

- `skills/wanderlust-agentic-security/SKILL.md`

## Reference Specifications

- `../specs/03-product-workflows-and-guardrails.md` — product guardrails and constraints.
- `../specs/04-agentic-backend-plan.md` — backend implementation plan and progress log.
- `../specs/05-deployment.md` — deployment instructions.

## Backend Direction

- No end-user identity-provider. Backend receives `X-User-Id` header only.
- All persistent state is Flutter-local. Backend storage is local SQLite only.
- Google Cloud is only used for external API calls (Gemini/Vertex AI, Google Maps Platform).
- API keys are read from `.env`. No Secret Manager.
- Keep external tools narrow, validated, allowlisted, and least-privileged.
