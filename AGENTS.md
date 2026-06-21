# Backend Agent Instructions

Before changing backend agent workflows, ADK tools, prompts, MCP/tool integrations,
external-source ingestion, ACTIVE itinerary event handling, or API key handling,
load and follow:

- `skills/wanderlust-agentic-security/SKILL.md`

Backend direction:

- No end-user identity-provider. Backend receives `X-User-Id` header only.
- All persistent state is Flutter-local. Backend storage is in-memory only.
- Google Cloud is only used for external API calls (Gemini/Vertex AI, Google Maps Platform).
- API keys are read from `.env`. No Secret Manager.
- Keep external tools narrow, validated, allowlisted, and least-privileged.
