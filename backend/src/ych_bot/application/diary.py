"""Owner diary for proactive messaging."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ych_bot.infrastructure.database import SQLiteRepository


class DiaryError(ValueError):
    """Raised when a diary request is invalid."""


class DiaryService:
    def __init__(self, repository: SQLiteRepository, *, timezone_name: str) -> None:
        self._repository = repository
        self._timezone_name = timezone_name

    async def list_entries(self) -> list[dict[str, Any]]:
        return await self._repository.diary_entries()

    async def get(self, day_key: str) -> dict[str, Any] | None:
        _parse_day(day_key)
        return await self._repository.diary_entry(day_key)

    async def save(self, *, day_key: str, content: str) -> dict[str, Any]:
        _parse_day(day_key)
        if len(content) > 20_000:
            raise DiaryError("日记最多 2 万字")
        return await self._repository.upsert_diary_entry(
            day_key=day_key,
            content=content,
            now=datetime.now(UTC),
        )


def _parse_day(day_key: str) -> date:
    try:
        return date.fromisoformat(day_key)
    except ValueError as exc:
        raise DiaryError("日期必须是 YYYY-MM-DD") from exc
