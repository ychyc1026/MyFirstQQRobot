"""Choose diary / material / conversation-context copy for proactive sends."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.modeling import ChatGenerationRequest, ChatModelGateway, ModelMessage, ModelRole
from ych_bot.domain.models import ConversationKind
from ych_bot.domain.proactive import quiet_hours_decision
from ych_bot.infrastructure.database import SQLiteRepository

from .context import ConversationContextService
from .search import WebSearchClient
from .stats import StatsService

SYSTEM_CONTEXT = (
    "YCH 是这个机器人的开发者。机器人用来和 YCH 的好友聊天。"
    "写给好友的消息要自然，不要像系统通知，也不要原样粘贴日记标题。"
)


class ProactiveContentComposer:
    def __init__(
        self,
        repository: SQLiteRepository,
        stats: StatsService,
        gateway: ChatModelGateway,
        context: ConversationContextService,
        search: WebSearchClient,
        *,
        owner_qq: str,
        bot_qq: str,
        model_route: str,
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._stats = stats
        self._gateway = gateway
        self._context = context
        self._search = search
        self._owner_qq = owner_qq
        self._bot_qq = bot_qq
        self._model_route = model_route
        self._enabled = enabled

    async def run_daily(self, now: datetime | None = None) -> int:
        clock = now or datetime.now(UTC)
        if clock.tzinfo is None:
            raise ValueError("proactive content clock must be timezone-aware")
        today = clock.astimezone(self._stats.timezone).date().isoformat()
        await self._repository.expire_pending_diaries(before_day=today, now=clock)
        sent = 0
        for policy in await self._repository.enabled_proactive_policies():
            if await self._maybe_send(policy, clock, today):
                sent += 1
        return sent

    async def _maybe_send(self, policy: dict[str, Any], now: datetime, today: str) -> bool:
        user_qq = str(policy["user_qq"])
        if not policy.get("auto_content_enabled") and not policy.get("send_diary"):
            return False
        if policy.get("quiet_hours_enabled"):
            quiet = quiet_hours_decision(
                now,
                timezone_name=policy["timezone"],
                quiet_start=policy["quiet_start"],
                quiet_end=policy["quiet_end"],
            )
            if quiet.quiet:
                return False
        start_iso, end_iso = self._stats.day_bounds(today)
        source, seed = await self._pick_source(
            policy,
            today,
            user_qq=user_qq,
            start_iso=start_iso,
            end_iso=end_iso,
        )
        if source is None or not seed.strip():
            return False
        if source != "diary":
            if await self._repository.auto_content_sent_today(
                user_qq=user_qq,
                start_iso=start_iso,
                end_iso=end_iso,
            ):
                return False
            window = await self._repository.proactive_delivery_window(
                target_qq=user_qq,
                day_start=datetime.fromisoformat(start_iso),
                day_end=datetime.fromisoformat(end_iso),
            )
            if int(window["delivered_count"]) >= int(policy["daily_limit"]):
                return False
            last_enqueued = window.get("last_enqueued_at")
            interval = int(policy.get("minimum_interval_seconds") or 0)
            if last_enqueued and interval > 0:
                previous = datetime.fromisoformat(str(last_enqueued))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=UTC)
                if previous + timedelta(seconds=interval) > now:
                    return False
        content = await self._render(user_qq, source, seed)
        if not content.strip():
            return False
        await self._repository.enqueue_auto_proactive_message(
            target_qq=user_qq,
            content=content[:2000],
            content_source=source,
            timezone_name=policy["timezone"],
            created_by=self._owner_qq,
            now=now,
            day_key=today if source == "diary" else "",
        )
        return True

    async def _pick_source(
        self,
        policy: dict[str, Any],
        today: str,
        *,
        user_qq: str,
        start_iso: str,
        end_iso: str,
    ) -> tuple[str | None, str]:
        if policy.get("send_diary"):
            diary = await self._repository.diary_entry(today)
            if diary and diary.get("status") != "expired":
                text = str(diary.get("content") or "").strip()
                already = await self._repository.auto_content_sent_today(
                    user_qq=user_qq,
                    start_iso=start_iso,
                    end_iso=end_iso,
                    content_source="diary",
                )
                if text and not already:
                    return "diary", text
        if not policy.get("auto_content_enabled"):
            return None, ""
        materials = await self._repository.enabled_materials()
        if materials:
            chosen = random.choice(materials)
            body = f"{chosen.get('title')}\n{chosen.get('content')}"
            if self._search.enabled:
                extra = await self._search.search(str(chosen.get("title") or body[:80]))
                if extra:
                    body = f"{body}\n检索：{extra[:800]}"
            return "material", body
        assembled = await self._context.assemble(
            conversation_kind=ConversationKind.PRIVATE,
            peer_id=user_qq,
            actor_qq=user_qq,
            owner_qq=self._owner_qq,
            bot_qq=self._bot_qq,
        )
        memories = "；".join(
            item.get("value", "") for item in assembled.memories[:6] if item.get("value")
        )
        recent = await self._repository.chatlog_messages(
            kind="private",
            peer_id=user_qq,
            start_iso=start_iso,
            end_iso=end_iso,
            limit=8,
        )
        snippets = "；".join(
            item.get("plain_text", "") for item in recent if item.get("plain_text")
        )
        seed = " ".join(part for part in (memories, snippets) if part).strip()
        if not seed:
            return None, ""
        return "context", seed

    async def _render(self, user_qq: str, source: str, seed: str) -> str:
        if not self._enabled:
            if source == "diary":
                return f"今天想跟你说一件事。{seed.strip()[:800]}"
            return ""
        labels = {
            "diary": "这是开发者 YCH 今天的日记，请改写成发给这位好友的自然消息。",
            "material": "这是可以主动提起的材料，如果合适就用它开一个轻松话题。",
            "context": "根据最近对话和记忆，写一句自然的后续关心。",
        }
        result = await self._gateway.generate(
            ChatGenerationRequest(
                request_id=str(uuid4()),
                messages=(
                    ModelMessage(role=ModelRole.SYSTEM, content=SYSTEM_CONTEXT),
                    ModelMessage(
                        role=ModelRole.USER,
                        content=f"{labels[source]}\n对象 QQ：{user_qq}\n材料：\n{seed[:4000]}",
                    ),
                ),
                max_output_tokens=400,
                temperature=0.7,
            )
        )
        await self._repository.record_usage_run(
            run_id=str(uuid4()),
            source_message_id=f"proactive:{source}:{user_qq}",
            conversation_key=f"{self._bot_qq}:private:{user_qq}",
            actor_qq=user_qq,
            bot_qq=self._bot_qq,
            mode=f"proactive_{source}",
            model_route=self._model_route or "chat",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return (result.text or "").strip()
