"""QQ Zone scheduling policy types."""

from __future__ import annotations

from dataclasses import dataclass

from ych_bot.domain.proactive import QuietHoursBehavior, parse_clock_time, timezone


@dataclass(frozen=True, slots=True)
class QzoneSchedulePolicy:
    timezone: str
    quiet_hours_enabled: bool
    quiet_start: str
    quiet_end: str
    quiet_behavior: QuietHoursBehavior
    daily_limit: int
    minimum_interval_seconds: int

    def validate(self) -> None:
        timezone(self.timezone)
        parse_clock_time(self.quiet_start)
        parse_clock_time(self.quiet_end)
        if self.quiet_start == self.quiet_end:
            raise ValueError("quiet hours start and end cannot be equal")
        if not (1 <= self.daily_limit <= 20):
            raise ValueError("daily_limit must be between 1 and 20")
        if not (0 <= self.minimum_interval_seconds <= 604_800):
            raise ValueError("minimum_interval_seconds must be between 0 and 604800")
