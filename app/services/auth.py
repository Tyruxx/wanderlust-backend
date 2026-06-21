from __future__ import annotations

from collections.abc import Mapping

import firebase_admin
from firebase_admin import credentials, auth
from pydantic import BaseModel

from app.core.settings import get_settings


class VerifiedUser(BaseModel):
    uid: str
    email: str | None = None
    phone_number: str | None = None
    name: str | None = None
    picture: str | None = None


class FirebaseAuthService:
    _initialized = False

    def __init__(self) -> None:
        if not self._initialized:
            settings = get_settings()
            if settings.app_env != "test":
                try:
                    firebase_admin.get_app()
                except ValueError:
                    cred = credentials.ApplicationDefault()
                    firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
            FirebaseAuthService._initialized = True
        self._auth = auth

    def verify_token(self, token: str) -> VerifiedUser:
        decoded: Mapping[str, object] = self._auth.verify_id_token(token)
        return VerifiedUser(
            uid=str(decoded.get("uid", "")),
            email=decoded.get("email"),
            phone_number=decoded.get("phone_number"),
            name=decoded.get("name"),
            picture=decoded.get("picture"),
        )

    def get_user(self, uid: str) -> VerifiedUser:
        record = self._auth.get_user(uid)
        return VerifiedUser(
            uid=record.uid,
            email=record.email,
            phone_number=record.phone_number,
            name=record.display_name,
            picture=record.photo_url,
        )


class MockFirebaseAuthService:
    def __init__(self, users: dict[str, VerifiedUser] | None = None) -> None:
        self.users = users or {}
        self.verified_tokens: dict[str, str] = {}

    def set_token(self, token: str, uid: str) -> None:
        self.verified_tokens[token] = uid

    def verify_token(self, token: str) -> VerifiedUser:
        uid = self.verified_tokens.get(token)
        if uid and uid in self.users:
            return self.users[uid]
        raise ValueError("Invalid token or user not found")

    def get_user(self, uid: str) -> VerifiedUser:
        if uid in self.users:
            return self.users[uid]
        raise ValueError(f"User {uid} not found")
