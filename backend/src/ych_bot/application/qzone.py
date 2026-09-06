"""Owner-controlled QQ Zone drafts and publish requests."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.control import QzonePostStatus, QzoneVisibility
from ych_bot.domain.proactive import MissedTaskPolicy, QuietHoursBehavior, timezone
from ych_bot.domain.qzone import QzoneSchedulePolicy
from ych_bot.infrastructure.database import SQLiteRepository

_TARGET_UIN_PATTERN = re.compile(r"^\d{5,15}$")
_MAX_TARGET_UINS = 50


class QzoneTaskError(ValueError):
    """Raised when a QQ Zone request is invalid or unavailable."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class QzoneTaskService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        owner_qq: str,
        default_timezone: str,
        default_quiet_start: str = "22:00",
        default_quiet_end: str = "08:00",
        default_daily_limit: int = 1,
        default_minimum_interval_seconds: int = 21_600,
        max_future_days: int = 366,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._owner_qq = owner_qq
        self._default_timezone = default_timezone
        self._clock = clock
        self._max_future_days = max_future_days
        self._default_policy = QzoneSchedulePolicy(
            timezone=default_timezone,
            quiet_hours_enabled=True,
            quiet_start=default_quiet_start,
            quiet_end=default_quiet_end,
            quiet_behavior=QuietHoursBehavior.DELAY,
            daily_limit=default_daily_limit,
            minimum_interval_seconds=default_minimum_interval_seconds,
        )
        self._default_policy.validate()
        if not (1 <= max_future_days <= 3660):
            raise ValueError("qzone max future days must be between 1 and 3660")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise QzoneTaskError("clock must return an aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _content(value: str) -> str:
        content = value.strip()
        if not content:
            raise QzoneTaskError("space content cannot be empty")
        if len(content) > 2000:
            raise QzoneTaskError("space content cannot exceed 2000 characters")
        return content

    @staticmethod
    def _target_uins(
        visibility: QzoneVisibility,
        target_uins: Sequence[str] | None,
    ) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for raw in target_uins or ():
            qq = str(raw).strip()
            if not qq:
                continue
            if not _TARGET_UIN_PATTERN.fullmatch(qq):
                raise QzoneTaskError("target QQ numbers must be 5 to 15 digits")
            if qq in seen:
                continue
            seen.add(qq)
            values.append(qq)
        if len(values) > _MAX_TARGET_UINS:
            raise QzoneTaskError("a space post cannot target more than 50 QQ numbers")
        needs_targets = visibility in {
            QzoneVisibility.SELECTED_FRIENDS,
            QzoneVisibility.EXCLUDED_FRIENDS,
        }
        if needs_targets and not values:
            raise QzoneTaskError("selected or excluded visibility requires target QQ numbers")
        if not needs_targets and values:
            raise QzoneTaskError(
                "target QQ numbers are only valid for selected or excluded visibility"
            )
        return tuple(values)

    async def create_draft(
        self,
        *,
        content: str,
        created_by: str,
        source: str,
        visibility: QzoneVisibility = QzoneVisibility.FRIENDS,
        target_uins: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        post_id = str(uuid4())
        await self._repository.create_qzone_draft(
            post_id=post_id,
            content=self._content(content),
            visibility=visibility,
            target_uins=self._target_uins(visibility, target_uins),
            source=source,
            created_by=created_by,
            timezone_name=self._default_timezone,
            now=self._now(),
        )
        return await self.post(post_id)

    async def request_publish(
        self,
        *,
        content: str,
        scheduled_for: datetime | None,
        timezone_name: str | None,
        created_by: str,
        source: str,
        visibility: QzoneVisibility = QzoneVisibility.FRIENDS,
        target_uins: Sequence[str] | None = None,
        missed_policy: MissedTaskPolicy = MissedTaskPolicy.REQUIRE_REAPPROVAL,
        missed_grace_seconds: int = 900,
    ) -> dict[str, Any]:
        now = self._now()
        scheduled = scheduled_for or now
        if scheduled.tzinfo is None:
            raise QzoneTaskError("scheduled_for must include a UTC offset")
        scheduled = scheduled.astimezone(UTC)
        timezone_value = timezone_name or self._default_timezone
        timezone(timezone_value)
        if scheduled > now + timedelta(days=self._max_future_days):
            raise QzoneTaskError("scheduled_for is beyond the configured future limit")
        if scheduled < now - timedelta(days=7):
            raise QzoneTaskError("scheduled_for is more than seven days in the past")
        if not (0 <= missed_grace_seconds <= 86_400):
            raise QzoneTaskError("missed_grace_seconds must be between 0 and 86400")
        post_id = str(uuid4())
        approval_id = str(uuid4())
        approval_code = secrets.token_hex(3).upper()
        await self._repository.create_qzone_publish_request(
            post_id=post_id,
            content=self._content(content),
            visibility=visibility,
            target_uins=self._target_uins(visibility, target_uins),
            scheduled_for=scheduled,
            timezone_name=timezone_value,
            missed_policy=missed_policy,
            missed_grace_seconds=missed_grace_seconds,
            source=source,
            created_by=created_by,
            approval_id=approval_id,
            approval_code=approval_code,
            requested_to=self._owner_qq,
            default_policy=self._default_policy,
            now=now,
        )
        return {
            **(await self.post(post_id)),
            "approval_code": approval_code,
            "approval_expires_in_minutes": 30,
        }

    async def request_from_local_time(
        self,
        *,
        content: str,
        scheduled_local: str | None,
        created_by: str,
        source: str,
    ) -> dict[str, Any]:
        scheduled: datetime | None = None
        if scheduled_local is not None:
            try:
                local_naive = datetime.strptime(scheduled_local, "%Y-%m-%d %H:%M")
            except ValueError as exc:
                raise QzoneTaskError("time must use YYYY-MM-DD HH:MM") from exc
            scheduled = local_naive.replace(tzinfo=timezone(self._default_timezone))
        return await self.request_publish(
            content=content,
            scheduled_for=scheduled,
            timezone_name=self._default_timezone,
            created_by=created_by,
            source=source,
        )

    async def post(self, post_id: str) -> dict[str, Any]:
        post = await self._repository.qzone_post(post_id)
        if post is None:
            raise QzoneTaskError("space post not found")
        return self._present(post)

    async def posts(
        self,
        *,
        status: QzonePostStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        for value in (date_from, date_to):
            if value is not None and value.tzinfo is None:
                raise QzoneTaskError("calendar bounds must include a UTC offset")
        if date_from and date_to and date_from >= date_to:
            raise QzoneTaskError("date_from must be earlier than date_to")
        posts = await self._repository.qzone_posts(
            status=status,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return [self._present(post) for post in posts]

    @staticmethod
    def _present(post: dict[str, Any]) -> dict[str, Any]:
        result = dict(post)
        zone = timezone(result["timezone"])
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

    async def request_revoke(
        self,
        post_id: str,
        *,
        actor_qq: str,
        source: str,
    ) -> dict[str, Any]:
        current = await self.post(post_id)
        if (
            current["status"] != QzonePostStatus.PUBLISHED.value
            or not str(current.get("qzone_tid") or "").strip()
        ):
            raise QzoneTaskError("space post cannot be revoked in its current state")
        approval_id = str(uuid4())
        approval_code = secrets.token_hex(3).upper()
        if not await self._repository.request_qzone_delete(
            post_id,
            actor_qq=actor_qq,
            source=source,
            approval_id=approval_id,
            approval_code=approval_code,
            requested_to=self._owner_qq,
            now=self._now(),
        ):
            raise QzoneTaskError("space post cannot be revoked in its current state")
        return {
            **(await self.post(post_id)),
            "approval_code": approval_code,
            "approval_expires_in_minutes": 30,
        }

    async def cancel(self, post_id: str, *, actor_qq: str) -> dict[str, Any]:
        if not await self._repository.cancel_qzone_post(post_id, actor_qq=actor_qq):
            raise QzoneTaskError("space post cannot be cancelled in its current state")
        return await self.post(post_id)

    async def policy(self) -> dict[str, Any]:
        stored = await self._repository.qzone_policy()
        if stored is not None:
            return {**stored, "persisted": True}
        return {
            "singleton_id": 1,
            "timezone": self._default_policy.timezone,
            "quiet_hours_enabled": self._default_policy.quiet_hours_enabled,
            "quiet_start": self._default_policy.quiet_start,
            "quiet_end": self._default_policy.quiet_end,
            "quiet_behavior": self._default_policy.quiet_behavior.value,
            "daily_limit": self._default_policy.daily_limit,
            "minimum_interval_seconds": self._default_policy.minimum_interval_seconds,
            "persisted": False,
        }

    async def update_policy(
        self,
        policy: QzoneSchedulePolicy,
        *,
        updated_by: str,
    ) -> dict[str, Any]:
        try:
            policy.validate()
        except ValueError as exc:
            raise QzoneTaskError(str(exc)) from exc
        await self._repository.upsert_qzone_policy(
            policy,
            updated_by=updated_by,
            now=self._now(),
        )
        return await self.policy()

    async def approve(self, post_id: str, *, actor_qq: str, late: bool = False) -> bool:
        return await self._repository.approve_qzone_post(
            post_id,
            actor_qq=actor_qq,
            approved_at=self._now(),
            late=late,
        )

    async def approve_delete(self, post_id: str, *, actor_qq: str) -> bool:
        return await self._repository.approve_qzone_delete(
            post_id,
            actor_qq=actor_qq,
            approved_at=self._now(),
        )

    async def reject(self, post_id: str, *, actor_qq: str) -> bool:
        return await self._repository.decide_qzone_post(
            post_id,
            status=QzonePostStatus.REJECTED,
            actor_qq=actor_qq,
            decided_at=self._now(),
        )

    async def reject_delete(self, post_id: str, *, actor_qq: str) -> bool:
        return await self._repository.revert_qzone_delete_request(
            post_id,
            actor_qq=actor_qq,
            reason="rejected",
            now=self._now(),
        )

    async def expire(self, post_id: str) -> bool:
        return await self._repository.decide_qzone_post(
            post_id,
            status=QzonePostStatus.APPROVAL_EXPIRED,
            actor_qq=None,
            decided_at=self._now(),
        )

    async def expire_delete(self, post_id: str) -> bool:
        return await self._repository.revert_qzone_delete_request(
            post_id,
            actor_qq=None,
            reason="approval_expired",
            now=self._now(),
        )
