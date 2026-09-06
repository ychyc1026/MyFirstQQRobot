"""Inbound event use case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.control import parse_owner_command
from ych_bot.domain.errors import IgnoredEvent
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat import parse_message_event

from .control import OwnerControlService
from .inference import ShadowReplyService
from .reply_orchestration import ReplyOrchestrationService


@dataclass(frozen=True, slots=True)
class IngestionResult:
    status: str
    reason: str
    message_id: str | None = None


class MessageIngestionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        bot_qq: str,
        enabled: bool,
        owner_qq: str | None = None,
        control_service: OwnerControlService | None = None,
        shadow_reply_service: ShadowReplyService | None = None,
        reply_orchestration_service: ReplyOrchestrationService | None = None,
        reply_runtime_control_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.bot_qq = bot_qq
        self.enabled = enabled
        self.owner_qq = owner_qq
        self.control_service = control_service
        self.shadow_reply_service = shadow_reply_service
        self.reply_orchestration_service = reply_orchestration_service
        self.reply_runtime_control_service = reply_runtime_control_service

    async def ingest(self, payload: dict[str, Any]) -> IngestionResult:
        if not self.enabled:
            return IngestionResult(status="ignored", reason="ingestion_disabled")

        if payload.get("post_type") == "meta_event":
            claimed = str(payload.get("self_id") or "").strip()
            if not claimed or not claimed.isdigit():
                return IngestionResult(status="ignored", reason="missing_self_id")
            if claimed != self.bot_qq:
                return IngestionResult(status="ignored", reason="foreign_bot_event")
            return IngestionResult(status="observed", reason="meta_event")

        if payload.get("post_type") == "notice":
            return await self._ingest_notice(payload)

        bots = await self.repository.list_bots()
        if bots:
            allowed = {bot["qq"] for bot in bots if bot["enabled"]}
        else:
            allowed = {self.bot_qq} if self.bot_qq else set()
        try:
            message = parse_message_event(payload, allowed_bot_qqs=allowed)
        except IgnoredEvent as error:
            return IngestionResult(status="ignored", reason=error.reason)

        inserted = await self.repository.store_inbound(message)
        if not inserted:
            return IngestionResult(
                status="duplicate",
                reason="event_already_stored",
                message_id=message.id,
            )
        owner_qq = await self.repository.current_owner_qq() or self.owner_qq
        if owner_qq:
            runtime_result = await self._reply_runtime_command(message, owner_qq)
            if runtime_result is not None:
                return runtime_result
            command = parse_owner_command(message, owner_qq=owner_qq)
            if command is not None and self.control_service is not None:
                control_result = await self.control_service.execute(command)
                return IngestionResult(
                    status="stored",
                    reason=f"owner_command:{control_result.status}",
                    message_id=message.id,
                )
        if self.reply_orchestration_service is not None:
            registered = await self.reply_orchestration_service.register_message(message)
            return IngestionResult(
                status="stored",
                reason=("reply_deferred" if registered.get("deferred") else "reply_registered"),
                message_id=message.id,
            )
        if self.shadow_reply_service is not None:
            shadow = await self.shadow_reply_service.generate(message)
            if shadow.status != "skipped":
                return IngestionResult(
                    status="stored",
                    reason=f"shadow:{shadow.status}",
                    message_id=message.id,
                )
        return IngestionResult(status="stored", reason="observe_only", message_id=message.id)

    async def _reply_runtime_command(self, message: Any, owner_qq: str) -> IngestionResult | None:
        if self.reply_runtime_control_service is None:
            return None
        if message.sender_id != owner_qq or message.conversation_kind.value != "private":
            return None
        text = message.plain_text.strip()
        if not text.startswith("/机器人"):
            return None
        parts = text.split()
        try:
            if parts == ["/机器人", "状态"]:
                await self.reply_runtime_control_service.status()
            elif parts == ["/机器人", "暂停"]:
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq, action="emergency_stop", payload={}
                )
            elif parts == ["/机器人", "恢复"]:
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq, action="resume", payload={}
                )
            elif len(parts) == 3 and parts[:2] == ["/机器人", "模式"]:
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq,
                    action="set_mode",
                    payload={"mode": parts[2]},
                )
            elif len(parts) == 3 and parts[:2] in (
                ["/机器人", "批准"],
                ["/机器人", "拒绝"],
            ):
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq,
                    action="decide_approval",
                    payload={
                        "approval_id": parts[2],
                        "approve": parts[1] == "批准",
                    },
                )
            elif len(parts) == 3 and parts[:2] == ["/机器人", "取消"]:
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq,
                    action="cancel_approval",
                    payload={"approval_id": parts[2]},
                )
            elif len(parts) == 3 and parts[:2] in (
                ["/机器人", "允许"],
                ["/机器人", "移除"],
            ):
                await self.reply_runtime_control_service.owner_action(
                    actor_qq=owner_qq,
                    action="set_eligibility",
                    payload={
                        "conversation_key": parts[2],
                        "enabled": parts[1] == "允许",
                    },
                )
            else:
                return None
        except (ValueError, RuntimeError):
            return IngestionResult(
                status="stored", reason="reply_runtime_command:rejected", message_id=message.id
            )
        return IngestionResult(
            status="stored", reason="reply_runtime_command:completed", message_id=message.id
        )

    async def _ingest_notice(self, payload: dict[str, Any]) -> IngestionResult:
        if payload.get("notice_type") != "friend_add":
            return IngestionResult(status="ignored", reason="unsupported_notice_type")
        if str(payload.get("self_id", "")) != self.bot_qq:
            return IngestionResult(status="ignored", reason="foreign_bot_event")

        user_qq = str(payload.get("user_id", "")).strip()
        if not user_qq:
            return IngestionResult(status="ignored", reason="missing_user_id")
        try:
            occurred_at = datetime.fromtimestamp(int(payload.get("time")), tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            occurred_at = datetime.now(UTC)

        event_key = (
            f"onebot:{self.bot_qq}:notice:friend_add:{user_qq}:{int(occurred_at.timestamp())}"
        )
        state, inserted = await self.repository.record_friend_add(
            bot_qq=self.bot_qq,
            user_qq=user_qq,
            occurred_at=occurred_at,
            event_key=event_key,
            raw_event=payload,
        )
        return IngestionResult(
            status="stored" if inserted else "duplicate",
            reason=f"friend_add:{state.value}",
            message_id=None,
        )
