"""Translate OneBot v11 payloads into transport-independent domain messages."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ych_bot.domain.errors import IgnoredEvent
from ych_bot.domain.models import (
    ConversationKind,
    MessageDirection,
    MessageSegment,
    UnifiedMessage,
)


def _as_id(value: Any, *, field: str) -> str:
    if value is None:
        raise IgnoredEvent(f"missing_{field}")
    normalized = str(value).strip()
    if not normalized:
        raise IgnoredEvent(f"missing_{field}")
    return normalized


def _segments(message: Any) -> tuple[MessageSegment, ...]:
    if isinstance(message, str):
        return (MessageSegment(type="text", data={"text": message}),)
    if not isinstance(message, list):
        raise IgnoredEvent("unsupported_message_format")

    normalized: list[MessageSegment] = []
    for item in message:
        if not isinstance(item, dict):
            continue
        segment_type = str(item.get("type", "unknown"))
        data = item.get("data", {})
        normalized.append(
            MessageSegment(
                type=segment_type,
                data=dict(data) if isinstance(data, dict) else {"value": data},
            )
        )
    return tuple(normalized)


def _event_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def parse_message_event(
    payload: dict[str, Any],
    *,
    expected_bot_qq: str | None = None,
    allowed_bot_qqs: set[str] | None = None,
) -> UnifiedMessage:
    if payload.get("post_type") != "message":
        raise IgnoredEvent("not_a_message_event")

    bot_qq = _as_id(payload.get("self_id"), field="self_id")
    allowed = set(allowed_bot_qqs or ())
    if expected_bot_qq:
        allowed.add(expected_bot_qq)
    if bot_qq not in allowed:
        raise IgnoredEvent("foreign_bot_event")

    message_type = str(payload.get("message_type", ""))
    sender_id = _as_id(payload.get("user_id"), field="user_id")
    if sender_id == bot_qq:
        raise IgnoredEvent("self_message")

    if message_type == "private":
        conversation_kind = ConversationKind.PRIVATE
        conversation_id = sender_id
    elif message_type == "group":
        conversation_kind = ConversationKind.GROUP
        conversation_id = _as_id(payload.get("group_id"), field="group_id")
    else:
        raise IgnoredEvent("unsupported_message_type")

    source_message_id = str(payload.get("message_id") or _event_fingerprint(payload))
    event_key = f"onebot:{bot_qq}:message:{message_type}:{source_message_id}"
    timestamp = payload.get("time")
    try:
        occurred_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        occurred_at = datetime.now(UTC)

    segments = _segments(payload.get("message", payload.get("raw_message", "")))
    reply_to = next(
        (
            str(segment.data["id"])
            for segment in segments
            if segment.type == "reply" and segment.data.get("id") is not None
        ),
        None,
    )

    return UnifiedMessage(
        id=str(uuid5(NAMESPACE_URL, event_key)),
        event_key=event_key,
        source_message_id=source_message_id,
        bot_qq=bot_qq,
        direction=MessageDirection.INBOUND,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        sender_id=sender_id,
        occurred_at=occurred_at,
        segments=segments,
        raw_event=payload,
        reply_to_message_id=reply_to,
    )
