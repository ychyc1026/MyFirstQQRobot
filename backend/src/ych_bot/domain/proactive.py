"""Safety policy and time calculations for proactive private messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ProactiveTaskStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    SCHEDULED = "scheduled"
    EVALUATING = "evaluating"
    WAITING_QUIET_HOURS = "waiting_quiet_hours"
    WAITING_RATE_LIMIT = "waiting_rate_limit"
    POLICY_BLOCKED = "policy_blocked"
    REAPPROVAL_REQUIRED = "reapproval_required"
    ENQUEUED = "enqueued"
    SENT = "sent"
    MISSED = "missed"
    REJECTED = "rejected"
    APPROVAL_EXPIRED = "approval_expired"
    CANCELLED = "cancelled"


class MissedTaskPolicy(StrEnum):
    SKIP = "skip"
    SEND_WITHIN_GRACE = "send_within_grace"
    REQUIRE_REAPPROVAL = "require_reapproval"


class QuietHoursBehavior(StrEnum):
    DELAY = "delay"
    REQUIRE_REAPPROVAL = "require_reapproval"


@dataclass(frozen=True, slots=True)
class ProactiveUserPolicy:
    user_qq: str
    enabled: bool
    timezone: str
    quiet_hours_enabled: bool
    quiet_start: str
    quiet_end: str
    quiet_behavior: QuietHoursBehavior
    daily_limit: int
    minimum_interval_seconds: int
    auto_content_enabled: bool = False
    send_diary: bool = False

    def validate(self) -> None:
        if not self.user_qq.isdigit() or not (5 <= len(self.user_qq) <= 20):
            raise ValueError("user_qq must contain 5 to 20 digits")
        timezone(self.timezone)
        parse_clock_time(self.quiet_start)
        parse_clock_time(self.quiet_end)
        if self.quiet_start == self.quiet_end:
            raise ValueError("quiet hours start and end cannot be equal")
        if not (1 <= self.daily_limit <= 100):
            raise ValueError("daily_limit must be between 1 and 100")
        if not (0 <= self.minimum_interval_seconds <= 86_400):
            raise ValueError("minimum_interval_seconds must be between 0 and 86400")


@dataclass(frozen=True, slots=True)
class QuietHoursDecision:
    quiet: bool
    next_allowed_at: datetime | None


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def parse_clock_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("quiet hour must use HH:MM") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("quiet hour must use HH:MM")
    return parsed


def quiet_hours_decision(
    now: datetime,
    *,
    timezone_name: str,
    quiet_start: str,
    quiet_end: str,
) -> QuietHoursDecision:
    """Return whether ``now`` is quiet and the first non-quiet UTC instant."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = timezone(timezone_name)
    local_now = now.astimezone(zone)
    start = parse_clock_time(quiet_start)
    end = parse_clock_time(quiet_end)
    current = local_now.timetz().replace(tzinfo=None)

    if start < end:
        quiet = start <= current < end
        end_date = local_now.date()
    else:
        quiet = current >= start or current < end
        end_date = local_now.date() + (timedelta(days=1) if current >= start else timedelta())

    if not quiet:
        return QuietHoursDecision(quiet=False, next_allowed_at=None)
    local_end = datetime.combine(end_date, end, tzinfo=zone)
    return QuietHoursDecision(quiet=True, next_allowed_at=local_end.astimezone(UTC))


def local_day_bounds(now: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    """Return the local calendar day's half-open UTC interval."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = timezone(timezone_name)
    local_date: date = now.astimezone(zone).date()
    start = datetime.combine(local_date, time.min, tzinfo=zone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)
