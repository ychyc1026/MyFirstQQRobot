"""Use cases for owner-controlled proactive private messages."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.proactive import (
    MissedTaskPolicy,
    ProactiveTaskStatus,
    ProactiveUserPolicy,
    QuietHoursBehavior,
    timezone,
)
from ych_bot.infrastructure.database import SQLiteRepository


class ProactiveMessageError(ValueError):
    """Raised when a proactive-message request is invalid or unavailable."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProactiveMessageService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        owner_qq: str,
        bot_qq: str,
        default_timezone: str,
        default_quiet_start: str = "22:00",
        default_quiet_end: str = "08:00",
        default_daily_limit: int = 3,
        default_minimum_interval_seconds: int = 3600,
        max_future_days: int = 366,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._owner_qq = owner_qq
        self._bot_qq = bot_qq
        self._default_timezone = default_timezone
        self._default_quiet_start = default_quiet_start
        self._default_quiet_end = default_quiet_end
        self._default_daily_limit = default_daily_limit
        self._default_minimum_interval_seconds = default_minimum_interval_seconds
        self._max_future_days = max_future_days
        self._clock = clock
        self._default_policy(self._owner_qq).validate()
        if not (1 <= max_future_days <= 3660):
            raise ValueError("max_future_days must be between 1 and 3660")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ProactiveMessageError("clock must return an aware datetime")
        return value.astimezone(UTC)

    def _default_policy(self, user_qq: str) -> ProactiveUserPolicy:
        return ProactiveUserPolicy(
            user_qq=user_qq,
            enabled=False,
            timezone=self._default_timezone,
            quiet_hours_enabled=True,
            quiet_start=self._default_quiet_start,
            quiet_end=self._default_quiet_end,
            quiet_behavior=QuietHoursBehavior.DELAY,
            daily_limit=self._default_daily_limit,
            minimum_interval_seconds=self._default_minimum_interval_seconds,
            auto_content_enabled=False,
            send_diary=False,
        )

    async def create_task(
        self,
        *,
        target_qq: str,
        content: str,
        scheduled_for: datetime,
        timezone_name: str,
        missed_policy: MissedTaskPolicy = MissedTaskPolicy.SKIP,
        missed_grace_seconds: int = 900,
        created_by: str,
        source: str,
    ) -> dict[str, Any]:
        target_qq = target_qq.strip()
        content = content.strip()
        if not target_qq.isdigit() or not (5 <= len(target_qq) <= 20):
            raise ProactiveMessageError("target_qq must contain 5 to 20 digits")
        if target_qq == self._bot_qq:
            raise ProactiveMessageError("the bot cannot proactively message itself")
        if not content:
            raise ProactiveMessageError("message content cannot be empty")
        if len(content) > 2000:
            raise ProactiveMessageError("message content cannot exceed 2000 characters")
        if scheduled_for.tzinfo is None:
            raise ProactiveMessageError("scheduled_for must include a UTC offset")
        timezone(timezone_name)
        if not (0 <= missed_grace_seconds <= 86_400):
            raise ProactiveMessageError("missed_grace_seconds must be between 0 and 86400")
        now = self._now()
        scheduled_utc = scheduled_for.astimezone(UTC)
        if scheduled_utc > now + timedelta(days=self._max_future_days):
            raise ProactiveMessageError("scheduled_for is beyond the configured future limit")
        if scheduled_utc < now - timedelta(days=7):
            raise ProactiveMessageError("scheduled_for is more than seven days in the past")

        task_id = str(uuid4())
        approval_id = str(uuid4())
        approval_code = secrets.token_hex(3).upper()
        policy = self._default_policy(target_qq)
        await self._repository.create_proactive_task_with_approval(
            task_id=task_id,
            target_qq=target_qq,
            content=content,
            scheduled_for=scheduled_utc,
            timezone_name=timezone_name,
            missed_policy=missed_policy,
            missed_grace_seconds=missed_grace_seconds,
            created_by=created_by,
            source=source,
            approval_id=approval_id,
            approval_code=approval_code,
            requested_to=self._owner_qq,
            default_policy=policy,
            now=now,
        )
        task = await self.task(task_id)
        return {
            **task,
            "approval_code": approval_code,
            "approval_expires_in_minutes": 30,
        }

    async def create_from_local_time(
        self,
        *,
        target_qq: str,
        content: str,
        scheduled_local: str,
        created_by: str,
        source: str,
    ) -> dict[str, Any]:
        try:
            local_naive = datetime.strptime(scheduled_local, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ProactiveMessageError("time must use YYYY-MM-DD HH:MM") from exc
        scheduled = local_naive.replace(tzinfo=timezone(self._default_timezone))
        return await self.create_task(
            target_qq=target_qq,
            content=content,
            scheduled_for=scheduled,
            timezone_name=self._default_timezone,
            created_by=created_by,
            source=source,
        )

    async def task(self, task_id: str) -> dict[str, Any]:
        task = await self._repository.proactive_task(task_id)
        if task is None:
            raise ProactiveMessageError("proactive task not found")
        return self._present_task(task)

    async def tasks(
        self,
        *,
        target_qq: str | None = None,
        status: ProactiveTaskStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if target_qq is not None and (not target_qq.isdigit() or not (5 <= len(target_qq) <= 20)):
            raise ProactiveMessageError("target_qq must contain 5 to 20 digits")
        for value in (date_from, date_to):
            if value is not None and value.tzinfo is None:
                raise ProactiveMessageError("calendar bounds must include a UTC offset")
        if date_from is not None and date_to is not None and date_from >= date_to:
            raise ProactiveMessageError("date_from must be earlier than date_to")
        tasks = await self._repository.proactive_tasks(
            target_qq=target_qq,
            status=status,
            date_from=date_from.astimezone(UTC) if date_from else None,
            date_to=date_to.astimezone(UTC) if date_to else None,
            limit=limit,
        )
        return [self._present_task(task) for task in tasks]

    @staticmethod
    def _present_task(task: dict[str, Any]) -> dict[str, Any]:
        result = dict(task)
        try:
            zone = timezone(result["timezone"])
        except (KeyError, ValueError):
            return result
        for field in ("original_scheduled_for", "scheduled_for", "next_eligible_at"):
            value = result.get(field)
            if not value:
                result[f"{field}_local"] = None
                continue
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            result[f"{field}_local"] = parsed.astimezone(zone).isoformat()
        return result

    async def cancel(self, task_id: str, *, actor_qq: str) -> dict[str, Any]:
        cancelled = await self._repository.cancel_proactive_task(task_id, actor_qq=actor_qq)
        if not cancelled:
            raise ProactiveMessageError("task cannot be cancelled in its current state")
        return await self.task(task_id)

    async def policy(self, user_qq: str) -> dict[str, Any]:
        if not user_qq.isdigit() or not (5 <= len(user_qq) <= 20):
            raise ProactiveMessageError("user_qq must contain 5 to 20 digits")
        policy = await self._repository.proactive_user_policy(user_qq)
        if policy is not None:
            return policy
        default = self._default_policy(user_qq)
        return {
            "user_qq": default.user_qq,
            "enabled": default.enabled,
            "timezone": default.timezone,
            "quiet_hours_enabled": default.quiet_hours_enabled,
            "quiet_start": default.quiet_start,
            "quiet_end": default.quiet_end,
            "quiet_behavior": default.quiet_behavior.value,
            "daily_limit": default.daily_limit,
            "minimum_interval_seconds": default.minimum_interval_seconds,
            "auto_content_enabled": default.auto_content_enabled,
            "send_diary": default.send_diary,
            "persisted": False,
        }

    async def update_policy(
        self,
        *,
        user_qq: str,
        enabled: bool,
        timezone_name: str,
        quiet_hours_enabled: bool,
        quiet_start: str,
        quiet_end: str,
        quiet_behavior: QuietHoursBehavior,
        daily_limit: int,
        minimum_interval_seconds: int,
        updated_by: str,
        auto_content_enabled: bool = False,
        send_diary: bool = False,
    ) -> dict[str, Any]:
        policy = ProactiveUserPolicy(
            user_qq=user_qq,
            enabled=enabled,
            timezone=timezone_name,
            quiet_hours_enabled=quiet_hours_enabled,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            quiet_behavior=quiet_behavior,
            daily_limit=daily_limit,
            minimum_interval_seconds=minimum_interval_seconds,
            auto_content_enabled=auto_content_enabled,
            send_diary=send_diary,
        )
        try:
            policy.validate()
        except ValueError as exc:
            raise ProactiveMessageError(str(exc)) from exc
        await self._repository.upsert_proactive_user_policy(
            policy,
            updated_by=updated_by,
            now=self._now(),
        )
        return await self.policy(user_qq)

    async def set_policy_enabled(
        self,
        user_qq: str,
        *,
        enabled: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        existing = await self.policy(user_qq)
        return await self.update_policy(
            user_qq=user_qq,
            enabled=enabled,
            timezone_name=existing["timezone"],
            quiet_hours_enabled=bool(existing["quiet_hours_enabled"]),
            quiet_start=existing["quiet_start"],
            quiet_end=existing["quiet_end"],
            quiet_behavior=QuietHoursBehavior(existing["quiet_behavior"]),
            daily_limit=int(existing["daily_limit"]),
            minimum_interval_seconds=int(existing["minimum_interval_seconds"]),
            auto_content_enabled=bool(existing.get("auto_content_enabled", False)),
            send_diary=bool(existing.get("send_diary", False)),
            updated_by=updated_by,
        )

    async def set_auto_content_enabled(
        self,
        user_qq: str,
        *,
        enabled: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        existing = await self.policy(user_qq)
        return await self._update_content_flags(
            existing,
            auto_content_enabled=enabled,
            send_diary=bool(existing.get("send_diary", False)),
            updated_by=updated_by,
        )

    async def set_diary_enabled(
        self,
        user_qq: str,
        *,
        enabled: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        existing = await self.policy(user_qq)
        return await self._update_content_flags(
            existing,
            auto_content_enabled=bool(existing.get("auto_content_enabled", False)),
            send_diary=enabled,
            updated_by=updated_by,
        )

    async def _update_content_flags(
        self,
        existing: dict[str, Any],
        *,
        auto_content_enabled: bool,
        send_diary: bool,
        updated_by: str,
    ) -> dict[str, Any]:
        return await self.update_policy(
            user_qq=existing["user_qq"],
            enabled=bool(existing["enabled"]),
            timezone_name=existing["timezone"],
            quiet_hours_enabled=bool(existing["quiet_hours_enabled"]),
            quiet_start=existing["quiet_start"],
            quiet_end=existing["quiet_end"],
            quiet_behavior=QuietHoursBehavior(existing["quiet_behavior"]),
            daily_limit=int(existing["daily_limit"]),
            minimum_interval_seconds=int(existing["minimum_interval_seconds"]),
            auto_content_enabled=auto_content_enabled,
            send_diary=send_diary,
            updated_by=updated_by,
        )

    async def approve(self, task_id: str, *, actor_qq: str, late: bool = False) -> bool:
        return await self._repository.approve_proactive_task(
            task_id,
            actor_qq=actor_qq,
            approved_at=self._now(),
            late=late,
        )

    async def reject(self, task_id: str, *, actor_qq: str) -> bool:
        return await self._repository.reject_proactive_task(
            task_id,
            actor_qq=actor_qq,
            decided_at=self._now(),
        )

    async def expire_approval(self, task_id: str) -> bool:
        return await self._repository.expire_proactive_task_approval(
            task_id,
            expired_at=self._now(),
        )
