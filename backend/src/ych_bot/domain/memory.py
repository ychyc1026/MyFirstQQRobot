"""Evidence-aware memory types and safe retrieval rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .models import ConversationKind


class MemoryKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    RELATIONSHIP_EVENT = "relationship_event"
    CONVERSATION_SUMMARY = "conversation_summary"


class MemorySource(StrEnum):
    OWNER_MANUAL = "owner_manual"
    MESSAGE_DERIVED = "message_derived"
    DOCUMENT_DERIVED = "document_derived"
    QZONE_DERIVED = "qzone_derived"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class MemoryConflictResolution(StrEnum):
    KEEP_LEFT = "keep_left"
    KEEP_RIGHT = "keep_right"
    FORGET_BOTH = "forget_both"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    user_qq: str
    kind: MemoryKind
    key: str
    value: dict[str, Any]
    source: MemorySource
    confidence: float
    status: MemoryStatus
    expires_at: datetime | None = None


def retrievable_memories(
    memories: tuple[MemoryRecord, ...],
    *,
    conversation_kind: ConversationKind,
    peer_id: str,
    now: datetime | None = None,
) -> tuple[MemoryRecord, ...]:
    """Personal memories are available only in the matching private conversation."""
    if conversation_kind is not ConversationKind.PRIVATE:
        return ()
    current = now or datetime.now(UTC)
    return tuple(
        memory
        for memory in memories
        if memory.user_qq == peer_id
        and memory.status is MemoryStatus.ACTIVE
        and (memory.expires_at is None or memory.expires_at > current)
    )
