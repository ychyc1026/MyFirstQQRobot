"""Owner-configured per-user reply bubble style. Never sends QQ."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.reply_style import (
    MAX_BUBBLES,
    UserReplyStylePolicy,
    policy_from_record,
)
from ych_bot.infrastructure.database import SQLiteRepository


class ReplyStyleError(RuntimeError):
    pass


class ReplyStyleService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    async def policy(self, user_qq: str) -> dict[str, Any]:
        try:
            policy = policy_from_record(user_qq, None)
            policy.validate()
        except ValueError as exc:
            raise ReplyStyleError(str(exc)) from exc
        stored = await self._repository.reply_style_policy(user_qq)
        current = policy_from_record(user_qq, stored)
        return {
            "user_qq": current.user_qq,
            "min_bubbles": current.min_bubbles,
            "max_bubbles": current.max_bubbles,
            "sentence_min_chars": current.sentence_min_chars,
            "sentence_max_chars": current.sentence_max_chars,
            "max_allowed_bubbles": MAX_BUBBLES,
            "typical_max_bubbles": 5,
            "persisted": stored is not None,
            "outbound_attached": False,
        }

    async def update_policy(
        self,
        *,
        user_qq: str,
        min_bubbles: int,
        max_bubbles: int,
        sentence_min_chars: int,
        sentence_max_chars: int,
        updated_by: str,
    ) -> dict[str, Any]:
        policy = UserReplyStylePolicy(
            user_qq=user_qq,
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
            sentence_min_chars=sentence_min_chars,
            sentence_max_chars=sentence_max_chars,
        )
        try:
            policy.validate()
        except ValueError as exc:
            raise ReplyStyleError(str(exc)) from exc
        await self._repository.upsert_reply_style_policy(
            policy,
            updated_by=updated_by,
            now=datetime.now(UTC),
        )
        return await self.policy(user_qq)
