"""Memory administration with candidate, conflict, and forgetting safeguards."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from ych_bot.domain.memory import (
    MemoryConflictResolution,
    MemoryKind,
    MemorySource,
    retrievable_memories,
)
from ych_bot.domain.models import ConversationKind
from ych_bot.infrastructure.database import SQLiteRepository


class MemoryValidationError(ValueError):
    pass


class MemoryService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    async def create_manual(
        self,
        *,
        user_qq: str,
        kind: MemoryKind,
        key: str,
        value: dict[str, Any],
        created_by: str,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_key = key.strip()
        if not user_qq.isdigit():
            raise MemoryValidationError("user_qq must contain digits only")
        if not normalized_key or len(normalized_key) > 120:
            raise MemoryValidationError("key must contain 1 to 120 characters")
        if not value:
            raise MemoryValidationError("value must not be empty")
        return await self._repository.create_memory(
            memory_id=str(uuid4()),
            user_qq=user_qq,
            kind=kind,
            key=normalized_key,
            value=value,
            source=MemorySource.OWNER_MANUAL,
            confidence=1.0,
            created_by=created_by,
            expires_at=expires_at.isoformat() if expires_at else None,
        )

    async def context(
        self,
        *,
        conversation_kind: ConversationKind,
        peer_id: str,
    ) -> tuple[dict[str, Any], ...]:
        records = await self._repository.active_memory_records(peer_id)
        visible = retrievable_memories(
            records,
            conversation_kind=conversation_kind,
            peer_id=peer_id,
        )
        return tuple(
            {
                "id": record.id,
                "kind": record.kind.value,
                "key": record.key,
                "value": record.value,
                "confidence": record.confidence,
                "source": record.source.value,
            }
            for record in visible
        )

    async def resolve_conflict(
        self,
        *,
        conflict_id: str,
        resolution: MemoryConflictResolution,
        resolved_by: str,
    ) -> bool:
        return await self._repository.resolve_memory_conflict(
            conflict_id,
            resolution=resolution,
            resolved_by=resolved_by,
        )
