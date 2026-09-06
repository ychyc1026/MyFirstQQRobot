"""Stable domain types shared by every transport and model provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def safe_model_image_url(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if value.startswith(("https://", "http://", "data:image/")):
        return value
    return None


def safe_napcat_image_file_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > 512:
        return None
    if value.startswith(("https://", "http://", "data:", "file:", "C:", "/", "\\")):
        return None
    if any(token in value for token in ("..", "\n", "\r", "\x00")):
        return None
    return value


class ConversationKind(StrEnum):
    PRIVATE = "private"
    GROUP = "group"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"
    DELIVERY_UNKNOWN = "delivery_unknown"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MessageSegment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_onebot(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


@dataclass(frozen=True, slots=True)
class UnifiedMessage:
    id: str
    event_key: str
    source_message_id: str
    bot_qq: str
    direction: MessageDirection
    conversation_kind: ConversationKind
    conversation_id: str
    sender_id: str
    occurred_at: datetime
    segments: tuple[MessageSegment, ...]
    raw_event: dict[str, Any]
    reply_to_message_id: str | None = None

    @property
    def conversation_key(self) -> str:
        return f"{self.bot_qq}:{self.conversation_kind.value}:{self.conversation_id}"

    @property
    def plain_text(self) -> str:
        return "".join(
            str(segment.data.get("text", "")) for segment in self.segments if segment.type == "text"
        ).strip()

    @property
    def mentioned_qq_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for segment in self.segments:
            if segment.type != "at":
                continue
            raw = segment.data.get("qq")
            if raw is None:
                continue
            value = str(raw).strip()
            if value and value not in ids:
                ids.append(value)
        return tuple(ids)

    @property
    def mentions_bot(self) -> bool:
        return self.bot_qq in self.mentioned_qq_ids

    @property
    def vision_image_urls(self) -> tuple[str, ...]:
        return tuple(url for url, _file_id in self.vision_image_pairs if url)

    @property
    def vision_image_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for segment in self.segments:
            if segment.type != "image":
                continue
            url = safe_model_image_url(segment.data.get("url")) or safe_model_image_url(
                segment.data.get("file")
            )
            file_id = safe_napcat_image_file_id(segment.data.get("file"))
            if url or file_id:
                pairs.append((url or "", file_id or ""))
        return tuple(pairs)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    id: str
    idempotency_key: str
    conversation_kind: ConversationKind
    target_id: str
    segments: tuple[MessageSegment, ...]
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
