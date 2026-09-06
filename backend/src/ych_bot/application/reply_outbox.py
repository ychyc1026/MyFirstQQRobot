"""Transactional handoff from a validated reply plan to the local outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ReplyOutboxServiceError(RuntimeError):
    """A validated reply plan could not be handed to the outbox safely."""


@dataclass(frozen=True, slots=True)
class ReplyOutboxHandoff:
    run_id: str
    outbox_ids: tuple[str, ...]
    created_count: int
    recovered: bool


class ReplyOutboxService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def handoff(
        self,
        *,
        run_id: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> ReplyOutboxHandoff:
        try:
            result = await self._repository.handoff_reply_plan_to_outbox(
                run_id=run_id,
                lease_token=lease_token,
                now=now,
            )
        except ValueError as exc:
            raise ReplyOutboxServiceError(str(exc)) from exc
        return ReplyOutboxHandoff(
            run_id=str(result["run_id"]),
            outbox_ids=tuple(str(item) for item in result["outbox_ids"]),
            created_count=int(result["created_count"]),
            recovered=bool(result["recovered"]),
        )
