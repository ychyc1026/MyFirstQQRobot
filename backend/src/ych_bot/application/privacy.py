"""Privacy enforcement gates placed in front of all history adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ych_bot.domain.readiness import CapabilityScope
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient

from .readiness_guard import ReadinessBlockedError, ReadinessGuard


@dataclass(frozen=True, slots=True)
class HistoryReadResult:
    allowed: bool
    reason: str
    messages: tuple[dict, ...] = ()


class NapCatHistoryReader:
    def __init__(
        self,
        repository: SQLiteRepository,
        client: NapCatClient,
        *,
        readiness_guard: ReadinessGuard,
    ) -> None:
        self.repository = repository
        self.client = client
        self._readiness_guard = readiness_guard

    async def read_private_history(
        self,
        *,
        user_qq: str,
        count: int,
        include_media: bool,
        purpose: str,
    ) -> HistoryReadResult:
        decision = await self.repository.authorize_history_access(
            user_qq=user_qq,
            requested_count=count,
            include_media=include_media,
            purpose=purpose,
            accessor="napcat.get_friend_msg_history",
            source_supports_range=False,
            consume=False,
        )
        if not decision["allowed"]:
            return HistoryReadResult(False, decision["reason"])

        try:
            await self._readiness_guard.require(
                CapabilityScope.LIVE_HISTORY,
                operation="history.read",
                operation_id=f"{user_qq}:{purpose}",
            )
        except ReadinessBlockedError as exc:
            return HistoryReadResult(False, f"readiness_{exc.blocker_codes[0]}")
        decision = await self.repository.authorize_history_access(
            user_qq=user_qq,
            requested_count=count,
            include_media=include_media,
            purpose=purpose,
            accessor="napcat.get_friend_msg_history",
            source_supports_range=False,
            consume=True,
        )
        if not decision["allowed"]:
            return HistoryReadResult(False, decision["reason"])
        messages = await self.client.get_friend_history(user_qq=user_qq, count=count)
        return HistoryReadResult(True, "authorized", tuple(messages))
