"""Observe-only orchestration for burst settling and durable run claiming."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.models import MessageDirection, UnifiedMessage
from ych_bot.domain.reply_pipeline import MessageEnvelope, ReplyRunStage


class ReplyOrchestrationError(RuntimeError):
    """A message cannot safely enter reply orchestration."""


class ReplyOrchestrationService:
    def __init__(self, repository: Any, *, settle_seconds: float = 1.5) -> None:
        if not 0 <= settle_seconds <= 30:
            raise ValueError("settle_seconds must be between 0 and 30")
        self._repository = repository
        self._settle_seconds = settle_seconds

    async def register_message(
        self,
        message: UnifiedMessage,
        *,
        observed_at: datetime | None = None,
        policy_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if message.direction is not MessageDirection.INBOUND:
            raise ReplyOrchestrationError("only inbound messages can trigger reply runs")
        now = _aware_utc(observed_at or datetime.now(UTC))
        result = await self._repository.create_or_append_reply_run(
            run_id=str(uuid4()),
            envelope=MessageEnvelope.from_unified(message),
            settled_until=now + timedelta(seconds=self._settle_seconds),
            policy_snapshot=policy_snapshot or {},
        )
        if result["created"]:
            transitioned = await self._repository.transition_reply_run(
                run_id=result["run"]["id"],
                expected_stage=ReplyRunStage.PENDING,
                target_stage=ReplyRunStage.SETTLING,
            )
            if not transitioned:
                raise ReplyOrchestrationError("new reply run could not enter settling")
            result["run"] = await self._repository.reply_run_detail(result["run"]["id"])
        return result

    async def claim_ready(
        self,
        *,
        worker_id: str,
        lease_ttl_seconds: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        return await self._repository.claim_ready_reply_run(
            lease_owner=worker_id,
            lease_token=str(uuid4()),
            ttl_seconds=lease_ttl_seconds,
            now=_aware_utc(now or datetime.now(UTC)),
        )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("reply orchestration timestamps must be timezone-aware")
    return value.astimezone(UTC)
