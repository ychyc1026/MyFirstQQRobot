"""Single-owner, multi-bot account partitions."""

from __future__ import annotations

from typing import Any

from ych_bot.infrastructure.database import SQLiteRepository

DEFAULT_PRIVATE_DAILY_TOKENS = 500_000
DEFAULT_GROUP_DAILY_TOKENS = 2_000_000
DEFAULT_USER_QUOTA_REPLY = "我今天的对话额度用完了，已经通知主号。主号加额后我才能继续和你聊。"


class AccountError(ValueError):
    pass


class AccountService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    async def ensure_seeded(self, *, owner_qq: str, bot_qq: str) -> None:
        await self.repository.ensure_account_seed(
            owner_qq=owner_qq,
            bot_qq=bot_qq,
            user_reply=DEFAULT_USER_QUOTA_REPLY,
            private_default=DEFAULT_PRIVATE_DAILY_TOKENS,
            group_default=DEFAULT_GROUP_DAILY_TOKENS,
        )

    async def snapshot(self) -> dict[str, Any]:
        owner = await self.repository.current_owner_qq()
        bots = await self.repository.list_bots()
        return {"owner_qq": owner, "bots": bots}

    async def set_owner(self, owner_qq: str, *, updated_by: str) -> dict[str, Any]:
        _require_digits(owner_qq, "owner_qq")
        bots = await self.repository.list_bots()
        if any(bot["qq"] == owner_qq for bot in bots):
            raise AccountError("owner_qq cannot equal a bot qq")
        await self.repository.set_owner_qq(owner_qq, updated_by=updated_by)
        return await self.snapshot()

    async def add_bot(self, bot_qq: str, *, label: str = "", updated_by: str) -> dict[str, Any]:
        _require_digits(bot_qq, "bot_qq")
        owner = await self.repository.current_owner_qq()
        if owner == bot_qq:
            raise AccountError("bot_qq cannot equal owner_qq")
        await self.repository.add_bot(
            bot_qq,
            label=label,
            updated_by=updated_by,
            user_reply=DEFAULT_USER_QUOTA_REPLY,
            private_default=DEFAULT_PRIVATE_DAILY_TOKENS,
            group_default=DEFAULT_GROUP_DAILY_TOKENS,
        )
        return await self.snapshot()

    async def disable_bot(self, bot_qq: str, *, updated_by: str) -> dict[str, Any]:
        await self.repository.set_bot_enabled(bot_qq, enabled=False, updated_by=updated_by)
        return await self.snapshot()

    async def enable_bot(self, bot_qq: str, *, updated_by: str) -> dict[str, Any]:
        await self.repository.set_bot_enabled(bot_qq, enabled=True, updated_by=updated_by)
        return await self.snapshot()

    async def update_bot(
        self,
        bot_qq: str,
        *,
        updated_by: str,
        label: str | None = None,
        quota_user_default: int | None = None,
        quota_group_default: int | None = None,
        quota_user_reply: str | None = None,
    ) -> dict[str, Any]:
        await self.repository.update_bot(
            bot_qq,
            updated_by=updated_by,
            label=label,
            quota_user_default=quota_user_default,
            quota_group_default=quota_group_default,
            quota_user_reply=quota_user_reply,
        )
        return await self.snapshot()


def _require_digits(value: str, field: str) -> None:
    if not value.isdigit():
        raise AccountError(f"{field} must contain digits only")
