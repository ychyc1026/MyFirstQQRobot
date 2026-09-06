"""Cached AI daily summaries. No realtime listening."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from ych_bot.domain.modeling import ChatGenerationRequest, ChatModelGateway, ModelMessage, ModelRole
from ych_bot.infrastructure.database import SQLiteRepository

from .stats import StatsService


class DailySummaryError(ValueError):
    """Raised when a daily summary cannot be produced."""


class DailySummaryService:
    def __init__(
        self,
        repository: SQLiteRepository,
        stats: StatsService,
        gateway: ChatModelGateway,
        *,
        owner_qq: str,
        bot_qq: str,
        model_route: str,
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._stats = stats
        self._gateway = gateway
        self._owner_qq = owner_qq
        self._bot_qq = bot_qq
        self._model_route = model_route
        self._enabled = enabled

    async def get(self, day_key: str) -> dict[str, Any] | None:
        _parse_day(day_key)
        return await self._repository.daily_summary(day_key)

    async def generate(self, day_key: str, *, force: bool = False) -> dict[str, Any]:
        _parse_day(day_key)
        cached = await self._repository.daily_summary(day_key)
        if cached is not None and not force:
            return cached
        start_iso, end_iso = self._stats.day_bounds(day_key)
        events = await self._repository.summary_conversation_events(
            start_iso=start_iso,
            end_iso=end_iso,
            limit=20,
        )
        entries = await self._summarize(day_key, events)
        saved = await self._repository.save_daily_summary(
            day_key=day_key,
            entries=entries,
            source_peer_ids=sorted(
                {
                    str(event["peer_id"])
                    for event in events
                    if str(event.get("peer_id") or "").strip()
                }
            ),
            model_route=self._model_route or "unconfigured",
            now=datetime.now(UTC),
        )
        return saved

    async def _summarize(
        self,
        day_key: str,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not events:
            return []
        if not self._enabled:
            return [_fallback_entry(event) for event in events[:20]]
        payload = json.dumps(events, ensure_ascii=False)
        request = ChatGenerationRequest(
            request_id=str(uuid4()),
            messages=(
                ModelMessage(
                    role=ModelRole.SYSTEM,
                    content=(
                        "你是 YCH 仪表盘的日摘要助手。根据给定会话事件写出最多 20 条时间线。"
                        "每条一行 JSON 数组，字段：time, alias, text。"
                        "alias 用对象 QQ 或群号。text 用中文，例如："
                        "小A 在 10:23 发送关于 XX 的问题，对话跨度 N 分钟，消耗 M token。"
                        "只输出 JSON 数组。"
                    ),
                ),
                ModelMessage(role=ModelRole.USER, content=f"日期 {day_key}\n{payload}"),
            ),
            max_output_tokens=1200,
            temperature=0.2,
        )
        result = await self._gateway.generate(request)
        await self._repository.record_usage_run(
            run_id=str(uuid4()),
            source_message_id=f"summary:{day_key}",
            conversation_key=f"{self._bot_qq}:private:{self._owner_qq}",
            actor_qq=self._owner_qq,
            bot_qq=self._bot_qq,
            mode="stats_summary",
            model_route=self._model_route or "stats",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        parsed = _parse_entries(result.text)
        if parsed:
            return parsed[:20]
        return [_fallback_entry(event) for event in events[:20]]


def _parse_day(day_key: str) -> date:
    try:
        return date.fromisoformat(day_key)
    except ValueError as exc:
        raise DailySummaryError("date must use YYYY-MM-DD") from exc


def _fallback_entry(event: dict[str, Any]) -> dict[str, str]:
    started = str(event.get("started_at") or "")[11:16]
    ended = str(event.get("ended_at") or "")
    started_full = str(event.get("started_at") or "")
    minutes = 0
    try:
        start_dt = datetime.fromisoformat(started_full)
        end_dt = datetime.fromisoformat(ended)
        minutes = max(0, int((end_dt - start_dt).total_seconds() // 60))
    except ValueError:
        minutes = 0
    peer = event.get("peer_id") or "未知"
    kind = "群" if event.get("kind") == "group" else "好友"
    snippet = str(event.get("first_user_text") or "消息").replace("\n", " ")[:40]
    tokens = int(event.get("tokens") or 0)
    return {
        "time": started or "--:--",
        "alias": str(peer),
        "text": (
            f"{kind} {peer} 在 {started or '未知时间'} 发送关于 {snippet} 的问题，"
            f"对话跨度 {minutes} 分钟，消耗 {tokens} token"
        ),
    }


def _parse_entries(text: str) -> list[dict[str, str]]:
    raw = text.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "time": str(item.get("time") or ""),
                "alias": str(item.get("alias") or ""),
                "text": str(item.get("text") or ""),
            }
        )
    return entries
