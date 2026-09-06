"""Owner-report delivery policy types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ych_bot.domain.control import ReportSeverity
from ych_bot.domain.proactive import parse_clock_time, timezone


class ReportDeliveryPolicy(StrEnum):
    IMMEDIATE = "immediate"
    DIGEST = "digest"
    DASHBOARD_ONLY = "dashboard_only"


class ReportDeliveryStatus(StrEnum):
    PENDING = "pending"
    WAITING_DIGEST = "waiting_digest"
    QUEUED = "queued"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class OwnerReportSchedulePolicy:
    timezone: str
    digest_local_time: str
    dedupe_window_seconds: int
    action_required_policy: ReportDeliveryPolicy
    critical_policy: ReportDeliveryPolicy
    warning_policy: ReportDeliveryPolicy
    info_policy: ReportDeliveryPolicy

    def validate(self) -> None:
        timezone(self.timezone)
        parse_clock_time(self.digest_local_time)
        if not (60 <= self.dedupe_window_seconds <= 86_400):
            raise ValueError("dedupe_window_seconds must be between 60 and 86400")

    def policy_for(self, severity: ReportSeverity | str) -> ReportDeliveryPolicy:
        key = severity.value if isinstance(severity, ReportSeverity) else severity
        mapping = {
            ReportSeverity.ACTION_REQUIRED.value: self.action_required_policy,
            ReportSeverity.CRITICAL.value: self.critical_policy,
            ReportSeverity.WARNING.value: self.warning_policy,
            ReportSeverity.INFO.value: self.info_policy,
        }
        return mapping.get(key, ReportDeliveryPolicy.DASHBOARD_ONLY)


DEFAULT_OWNER_REPORT_POLICY = OwnerReportSchedulePolicy(
    timezone="Asia/Shanghai",
    digest_local_time="09:00",
    dedupe_window_seconds=1_800,
    action_required_policy=ReportDeliveryPolicy.IMMEDIATE,
    critical_policy=ReportDeliveryPolicy.IMMEDIATE,
    warning_policy=ReportDeliveryPolicy.DIGEST,
    info_policy=ReportDeliveryPolicy.DIGEST,
)


def next_digest_at(
    now: datetime,
    *,
    timezone_name: str,
    digest_local_time: str,
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = timezone(timezone_name)
    local_now = now.astimezone(zone)
    digest_time = parse_clock_time(digest_local_time)
    candidate = local_now.replace(
        hour=digest_time.hour,
        minute=digest_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def default_dedupe_key(
    *,
    category: str,
    related_type: str | None,
    related_id: str | None,
    severity: str,
) -> str:
    return f"{category}:{related_type or '-'}:{related_id or '-'}:{severity}"
