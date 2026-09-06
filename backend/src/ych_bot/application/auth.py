"""Short-lived dashboard sessions derived from a local bootstrap secret."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ych_bot.infrastructure.database import SQLiteRepository


@dataclass(frozen=True, slots=True)
class AdminSession:
    token: str
    expires_at: str


class AdminSessionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        bootstrap_token: str,
        lifetime_hours: int,
    ) -> None:
        self._repository = repository
        self._bootstrap_token = bootstrap_token
        self._lifetime_hours = lifetime_hours

    @property
    def configured(self) -> bool:
        return bool(self._bootstrap_token)

    async def create(self, bootstrap_token: str, *, source: str) -> AdminSession | None:
        if not self.configured or not hmac.compare_digest(
            bootstrap_token,
            self._bootstrap_token,
        ):
            return None
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(hours=self._lifetime_hours)
        await self._repository.create_admin_session(
            session_id=str(uuid4()),
            token_hash=self._hash(raw_token),
            expires_at=expires_at.isoformat(),
            created_from=source,
        )
        return AdminSession(token=raw_token, expires_at=expires_at.isoformat())

    async def authenticate(self, raw_token: str) -> bool:
        if not raw_token:
            return False
        return await self._repository.validate_admin_session(self._hash(raw_token))

    async def revoke(self, raw_token: str) -> bool:
        if not raw_token:
            return False
        return await self._repository.revoke_admin_session(self._hash(raw_token))

    @staticmethod
    def _hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
