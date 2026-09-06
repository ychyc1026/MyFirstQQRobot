"""Coordinate reply-specific delivery evidence and terminal outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ych_bot.domain.models import OutboundMessage


@dataclass(frozen=True, slots=True)
class ReplyDeliveryAttempt:
    run_id: str
    bubble_sequence: int
    attempt: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReplyDeliveryOutcome:
    run_id: str
    outcome: str
    run_completed: bool
    recovered: bool


class ReplyDeliveryService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def begin(self, message: OutboundMessage) -> ReplyDeliveryAttempt | None:
        result = await self._repository.begin_reply_delivery(message.id)
        if result is None:
            return None
        if result["idempotency_key"] != message.idempotency_key:
            raise RuntimeError("reply delivery mapping does not match claimed outbox item")
        return ReplyDeliveryAttempt(
            run_id=str(result["run_id"]),
            bubble_sequence=int(result["bubble_sequence"]),
            attempt=int(result["attempt"]),
            idempotency_key=str(result["idempotency_key"]),
        )

    async def finish(
        self,
        message: OutboundMessage,
        *,
        outcome: str,
        safe_detail: str,
        provider_message_id: str | None = None,
    ) -> ReplyDeliveryOutcome | None:
        result = await self._repository.finalize_reply_delivery(
            outbox_id=message.id,
            outcome=outcome,
            safe_detail=safe_detail,
            provider_message_id=provider_message_id,
        )
        if result is None:
            return None
        return ReplyDeliveryOutcome(
            run_id=str(result["run_id"]),
            outcome=str(result["outcome"]),
            run_completed=bool(result["run_completed"]),
            recovered=bool(result["recovered"]),
        )
