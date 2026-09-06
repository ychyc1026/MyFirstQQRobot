"""Read-only dashboard statistics."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ych_bot.infrastructure.database import SQLiteRepository

StatsRange = Literal["today", "7d", "30d"]

RANGE_DAYS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}


class StatsService:
    def __init__(self, repository: SQLiteRepository, *, timezone_name: str) -> None:
        self._repository = repository
        self._timezone = ZoneInfo(timezone_name)
        self._timezone_name = timezone_name

    @property
    def timezone(self) -> ZoneInfo:
        return self._timezone

    def _today(self) -> date:
        return datetime.now(self._timezone).date()

    def bounds_for(self, range_key: str) -> tuple[str, str, date, date]:
        days = RANGE_DAYS.get(range_key, 1)
        end_date = self._today() + timedelta(days=1)
        start_date = end_date - timedelta(days=days)
        start = datetime.combine(start_date, time.min, tzinfo=self._timezone)
        end = datetime.combine(end_date, time.min, tzinfo=self._timezone)
        return (
            start.astimezone(UTC).isoformat(),
            end.astimezone(UTC).isoformat(),
            start_date,
            end_date,
        )

    def day_bounds(self, day_key: str) -> tuple[str, str]:
        local_day = date.fromisoformat(day_key)
        start = datetime.combine(local_day, time.min, tzinfo=self._timezone)
        end = start + timedelta(days=1)
        return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()

    async def overview(self, range_key: str = "today") -> dict[str, Any]:
        if range_key not in RANGE_DAYS:
            range_key = "today"
        start_iso, end_iso, start_date, end_date = self.bounds_for(range_key)
        counts = await self._repository.stats_message_counts(start_iso=start_iso, end_iso=end_iso)
        tokens = await self._repository.stats_token_leaders(start_iso=start_iso, end_iso=end_iso)
        daily = await self._repository.stats_daily_messages(
            start_iso=start_iso,
            end_iso=end_iso,
            timezone_name=self._timezone_name,
        )
        filled = []
        cursor = start_date
        by_day = {item["day"]: item for item in daily}
        while cursor < end_date:
            key = cursor.isoformat()
            point = by_day.get(key, {"day": key, "inbound": 0, "outbound": 0})
            filled.append(point)
            cursor += timedelta(days=1)
        return {
            "range": range_key,
            "timezone": self._timezone_name,
            "start_day": start_date.isoformat(),
            "end_day": (end_date - timedelta(days=1)).isoformat(),
            **counts,
            "trend": filled,
            "tokens": tokens,
        }

    async def chatlog_peers(self) -> list[dict[str, Any]]:
        start_iso, end_iso = self.day_bounds(self._today().isoformat())
        return await self._repository.chatlog_peers(start_iso=start_iso, end_iso=end_iso)

    async def chatlog(
        self,
        *,
        kind: str,
        peer_id: str,
        after_id: str = "",
    ) -> dict[str, Any]:
        if kind not in {"private", "group"}:
            raise ValueError("kind must be private or group")
        if not peer_id.isdigit():
            raise ValueError("peer_id must contain digits")
        start_iso, end_iso = self.day_bounds(self._today().isoformat())
        items = await self._repository.chatlog_messages(
            kind=kind,
            peer_id=peer_id,
            start_iso=start_iso,
            end_iso=end_iso,
            after_id=after_id,
        )
        return {
            "day": self._today().isoformat(),
            "kind": kind,
            "peer_id": peer_id,
            "items": items,
        }
