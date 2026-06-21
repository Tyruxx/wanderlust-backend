# Backend Agent Instructions

Before changing backend agent workflows, ADK tools, prompts, MCP/tool integrations,
external-source ingestion, secrets, deployment, persistence, or ACTIVE itinerary
event handling, load and follow:

- `skills/wanderlust-agentic-security/SKILL.md`

Backend direction:

- Do not add end-user identity-provider requirements.
- Treat Flutter as the owner of device-local preferences and saved itineraries.
- Backend APIs should receive explicit request context from the app.
- Keep external tools narrow, validated, allowlisted, and least-privileged.
