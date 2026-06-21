from __future__ import annotations

from pydantic import BaseModel


class VerifiedUser(BaseModel):
    uid: str
    email: str | None = None
    phone_number: str | None = None
    name: str | None = None
    picture: str | None = None
