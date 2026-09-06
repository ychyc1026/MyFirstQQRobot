"""Owner-report recording, policy and presentation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.control import ReportSeverity
from ych_bot.domain.reports import (
    DEFAULT_OWNER_REPORT_POLICY,
    OwnerReportSchedulePolicy,
    ReportDeliveryPolicy,
)
from ych_bot.infrastructure.database import SQLiteRepository


class OwnerReportError(ValueError):
    """Raised when an owner-report request is invalid."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OwnerReportService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        owner_qq: str,
        default_timezone: str,
        default_digest_local_time: str = "09:00",
        default_dedupe_window_seconds: int = 1_800,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._owner_qq = owner_qq
        self._clock = clock
        self._default_policy = OwnerReportSchedulePolicy(
            timezone=default_timezone,
            digest_local_time=default_digest_local_time,
            dedupe_window_seconds=default_dedupe_window_seconds,
            action_required_policy=DEFAULT_OWNER_REPORT_POLICY.action_required_policy,
            critical_policy=DEFAULT_OWNER_REPORT_POLICY.critical_policy,
            warning_policy=DEFAULT_OWNER_REPORT_POLICY.warning_policy,
            info_policy=DEFAULT_OWNER_REPORT_POLICY.info_policy,
        )
        self._default_policy.validate()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OwnerReportError("clock must return an aware datetime")
        return value.astimezone(UTC)

    async def record(
        self,
        *,
        severity: ReportSeverity,
        category: str,
        title: str,
        body: str,
        related_type: str | None = None,
        related_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._repository.record_owner_report(
            severity=severity,
            category=category,
            title=title,
            body=body,
            related_type=related_type,
            related_id=related_id,
            dedupe_key=dedupe_key,
            now=self._now(),
            updated_by=self._owner_qq,
        )

    async def report(self, report_id: str) -> dict[str, Any]:
        stored = await self._repository.owner_report(report_id)
        if stored is None:
            raise OwnerReportError("owner report not found")
        return stored

    async def queue(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repository.owner_report_queue(limit=limit)

    async def policy(self) -> dict[str, Any]:
        stored = await self._repository.owner_report_policy()
        if stored:
            return {**stored, "persisted": True}
        return {
            **self._default_policy.__dict__,
            "action_required_policy": self._default_policy.action_required_policy.value,
            "critical_policy": self._default_policy.critical_policy.value,
            "warning_policy": self._default_policy.warning_policy.value,
            "info_policy": self._default_policy.info_policy.value,
            "persisted": False,
        }

    async def update_policy(
        self,
        *,
        timezone_name: str | None = None,
        digest_local_time: str | None = None,
        dedupe_window_seconds: int | None = None,
        action_required_policy: ReportDeliveryPolicy | None = None,
        critical_policy: ReportDeliveryPolicy | None = None,
        warning_policy: ReportDeliveryPolicy | None = None,
        info_policy: ReportDeliveryPolicy | None = None,
        updated_by: str,
    ) -> dict[str, Any]:
        current = await self._repository.owner_report_policy()
        base = self._default_policy
        if current:
            base = OwnerReportSchedulePolicy(
                timezone=current["timezone"],
                digest_local_time=current["digest_local_time"],
                dedupe_window_seconds=int(current["dedupe_window_seconds"]),
                action_required_policy=ReportDeliveryPolicy(current["action_required_policy"]),
                critical_policy=ReportDeliveryPolicy(current["critical_policy"]),
                warning_policy=ReportDeliveryPolicy(current["warning_policy"]),
                info_policy=ReportDeliveryPolicy(current["info_policy"]),
            )
        policy = OwnerReportSchedulePolicy(
            timezone=timezone_name or base.timezone,
            digest_local_time=digest_local_time or base.digest_local_time,
            dedupe_window_seconds=dedupe_window_seconds or base.dedupe_window_seconds,
            action_required_policy=action_required_policy or base.action_required_policy,
            critical_policy=critical_policy or base.critical_policy,
            warning_policy=warning_policy or base.warning_policy,
            info_policy=info_policy or base.info_policy,
        )
        try:
            policy.validate()
        except ValueError as exc:
            raise OwnerReportError(str(exc)) from exc
        await self._repository.upsert_owner_report_policy(
            policy,
            updated_by=updated_by,
            now=self._now(),
        )
        return await self.policy()
