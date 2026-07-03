from __future__ import annotations

from pydantic import BaseModel, Field


class ManualCallRequest(BaseModel):
    venue_name: str = Field(min_length=1, max_length=200)
    venue_contact: str | None = Field(default=None, max_length=40)
    remarks: str | None = Field(default=None, max_length=500)


class ManualCallResponse(BaseModel):
    venue_contact: str | None
    contact_found: bool
    script: str | None = None
    agent_message: str


class ManualCallService:
    def prepare_response(
        self, request: ManualCallRequest, *, include_script: bool
    ) -> ManualCallResponse:
        contact_found = bool(request.venue_contact and request.venue_contact.strip())
        if not include_script:
            return ManualCallResponse(
                venue_contact=request.venue_contact if contact_found else None,
                contact_found=contact_found,
                agent_message=(
                    "I found a venue contact for you."
                    if contact_found
                    else "I could not find a venue contact."
                ),
            )

        script = self._build_script(request)
        return ManualCallResponse(
            venue_contact=request.venue_contact if contact_found else None,
            contact_found=contact_found,
            script=script,
            agent_message=(
                "Here is a concise script you can use with the venue."
                if contact_found
                else "I could not find a venue contact, but I prepared a script you can use."
            ),
        )

    def _build_script(self, request: ManualCallRequest) -> str:
        remarks = (
            request.remarks.strip()
            if request.remarks
            else "I would like to ask about availability."
        )
        return (
            f"Hello, I am calling about {request.venue_name}. "
            f"{remarks} Could you please help me with this?"
        )
