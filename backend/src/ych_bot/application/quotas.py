"""Per-bot private/group daily chat token quotas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ych_bot.domain.models import (
    ConversationKind,
    MessageSegment,
    OutboundMessage,
    OutboxStatus,
    UnifiedMessage,
)
from ych_bot.infrastructure.database import SQLiteRepository

from .accounts import (
    DEFAULT_GROUP_DAILY_TOKENS,
    DEFAULT_PRIVATE_DAILY_TOKENS,
    DEFAULT_USER_QUOTA_REPLY,
)

DEFAULT_PRIVATE_DAILY_TOKENS = DEFAULT_PRIVATE_DAILY_TOKENS
DEFAULT_GROUP_DAILY_TOKENS = DEFAULT_GROUP_DAILY_TOKENS
DEFAULT_USER_QUOTA_REPLY = DEFAULT_USER_QUOTA_REPLY


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    exceeded: bool
    peer_kind: str
    peer_id: str
    bot_qq: str
    used: int
    daily_limit: int
    today_bonus: int
    limit: int
    display_name: str
    user_reply: str


class ChatQuotaService:
    def __init__(self, repository: SQLiteRepository, *, timezone: str) -> None:
        self.repository = repository
        self.timezone = ZoneInfo(timezone)

    def _today_key(self) -> str:
        return datetime.now(self.timezone).date().isoformat()

    def _day_bounds(self) -> tuple[str, str]:
        today = datetime.now(self.timezone).date()
        start = datetime.combine(today, time.min, tzinfo=self.timezone).astimezone(UTC)
        end = start + timedelta(days=1)
        return start.isoformat(), end.isoformat()

    async def evaluate(self, message: UnifiedMessage) -> QuotaDecision:
        peer_kind = "group" if message.conversation_kind is ConversationKind.GROUP else "private"
        peer_id = message.conversation_id
        bot = await self.repository.bot_record(message.bot_qq)
        defaults = (
            (bot or {}).get("quota_group_default", DEFAULT_GROUP_DAILY_TOKENS)
            if peer_kind == "group"
            else (bot or {}).get("quota_user_default", DEFAULT_PRIVATE_DAILY_TOKENS)
        )
        override = await self.repository.chat_quota_override(message.bot_qq, peer_kind, peer_id)
        daily_limit = int(override["daily_limit"]) if override else int(defaults)
        today = self._today_key()
        today_bonus = 0
        display_name = ""
        if override:
            display_name = str(override.get("display_name") or "")
            if str(override.get("bonus_day") or "") == today:
                today_bonus = int(override.get("today_bonus") or 0)
        start_iso, end_iso = self._day_bounds()
        used = await self.repository.inference_token_usage(
            bot_qq=message.bot_qq,
            conversation_key=message.conversation_key,
            start_iso=start_iso,
            end_iso=end_iso,
        )
        limit = daily_limit + today_bonus
        return QuotaDecision(
            exceeded=used >= limit,
            peer_kind=peer_kind,
            peer_id=peer_id,
            bot_qq=message.bot_qq,
            used=used,
            daily_limit=daily_limit,
            today_bonus=today_bonus,
            limit=limit,
            display_name=display_name,
            user_reply=str((bot or {}).get("quota_user_reply") or DEFAULT_USER_QUOTA_REPLY),
        )

    async def handle_exceeded(
        self,
        message: UnifiedMessage,
        decision: QuotaDecision,
    ) -> dict[str, bool]:
        if decision.peer_kind == "group":
            return {"owner_notified": False, "user_replied": False}
        day_key = self._today_key()
        notice = await self.repository.quota_notice(
            decision.bot_qq, decision.peer_kind, decision.peer_id, day_key
        )
        owner_notified = False
        user_replied = False
        if not notice.get("owner_notified"):
            label = decision.display_name or "未备注"
            await self.repository.record_owner_report(
                severity="action_required",
                category="chat_quota",
                title="用户今日额度已用尽",
                body=(
                    f"机器人 {decision.bot_qq} 用户 {decision.peer_id}（{label}）"
                    f"今日聊天额度已用尽（已用 {decision.used} / 上限 {decision.limit}）。"
                    "可在仪表盘追加今天额度。"
                ),
                related_type="user",
                related_id=decision.peer_id,
                dedupe_key=f"chat_quota:{decision.bot_qq}:{decision.peer_id}:{day_key}",
            )
            owner_notified = True
        if not notice.get("user_replied"):
            await self.repository.enqueue_outbound(
                OutboundMessage(
                    id=str(uuid4()),
                    idempotency_key=f"quota-reply:{decision.bot_qq}:{decision.peer_id}:{day_key}",
                    conversation_kind=ConversationKind.PRIVATE,
                    target_id=decision.peer_id,
                    segments=(MessageSegment(type="text", data={"text": decision.user_reply}),),
                    status=OutboxStatus.PENDING,
                )
            )
            user_replied = True
        if owner_notified or user_replied:
            await self.repository.mark_quota_notice(
                decision.bot_qq,
                decision.peer_kind,
                decision.peer_id,
                day_key,
                owner_notified=bool(notice.get("owner_notified")) or owner_notified,
                user_replied=bool(notice.get("user_replied")) or user_replied,
            )
        return {"owner_notified": owner_notified, "user_replied": user_replied}

    async def add_today_bonus(
        self,
        *,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        amount: int,
        updated_by: str,
    ) -> dict[str, Any]:
        if amount < 1:
            raise ValueError("today bonus must be positive")
        day_key = self._today_key()
        result = await self.repository.add_today_quota_bonus(
            bot_qq=bot_qq,
            peer_kind=peer_kind,
            peer_id=peer_id,
            amount=amount,
            day_key=day_key,
            updated_by=updated_by,
            default_daily=(
                DEFAULT_GROUP_DAILY_TOKENS if peer_kind == "group" else DEFAULT_PRIVATE_DAILY_TOKENS
            ),
        )
        await self.repository.clear_quota_notice(bot_qq, peer_kind, peer_id, day_key)
        return result

    async def set_daily_limit(
        self,
        *,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        daily_limit: int,
        display_name: str | None = None,
        updated_by: str,
    ) -> dict[str, Any]:
        if daily_limit < 1:
            raise ValueError("daily_limit must be positive")
        return await self.repository.upsert_chat_quota_override(
            bot_qq=bot_qq,
            peer_kind=peer_kind,
            peer_id=peer_id,
            daily_limit=daily_limit,
            display_name=display_name,
            updated_by=updated_by,
        )

    async def list_quotas(
        self, *, bot_qq: str, peer_kind: str | None = None
    ) -> list[dict[str, Any]]:
        return await self.repository.list_chat_quota_overrides(bot_qq=bot_qq, peer_kind=peer_kind)
