"""Default-off real-calendar scheduler for approved proactive messages."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.proactive import (
    MissedTaskPolicy,
    ProactiveTaskStatus,
    QuietHoursBehavior,
    local_day_bounds,
    quiet_hours_decision,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.runtime import DurablePauseMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProactiveWorkerRunResult:
    status: str
    task_id: str | None = None
    reason: str | None = None


class ProactiveMessageWorker(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        owner_qq: str,
        configured_enabled: bool,
        poll_seconds: float,
        lease_seconds: int = 60,
        schedule_tolerance_seconds: int = 60,
        policy_recheck_seconds: int = 300,
        clock: Callable[[], datetime] = _utc_now,
        content_composer: Any | None = None,
    ) -> None:
        if not (0.1 <= poll_seconds <= 300):
            raise ValueError("proactive worker poll interval must be between 0.1 and 300 seconds")
        if not (10 <= lease_seconds <= 3600):
            raise ValueError("proactive worker lease must be between 10 and 3600 seconds")
        if not (0 <= schedule_tolerance_seconds <= 600):
            raise ValueError("schedule tolerance must be between 0 and 600 seconds")
        if not (10 <= policy_recheck_seconds <= 86_400):
            raise ValueError("policy recheck must be between 10 and 86400 seconds")
        self._repository = repository
        self._owner_qq = owner_qq
        self._configured_enabled = configured_enabled
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._schedule_tolerance_seconds = schedule_tolerance_seconds
        self._policy_recheck_seconds = policy_recheck_seconds
        self._clock = clock
        self._content_composer = content_composer
        self._pause_worker_name = "proactive"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_task_id: str | None = None
        self._last_content_scan_at: datetime | None = None
        self._last_result: str | None = None
        self._last_reason: str | None = None
        self._enqueued_tasks = 0
        self._held_tasks = 0
        self._missed_tasks = 0
        self._reapproval_tasks = 0

    @property
    def configured_enabled(self) -> bool:
        return self._configured_enabled

    @property
    def active(self) -> bool:
        self._ensure_pause_state()
        return self._configured_enabled and not self._paused

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("proactive worker clock must return an aware datetime")
        return value.astimezone(UTC)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self._configured_enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "currently_evaluating": self._run_lock.locked(),
            "poll_seconds": self._poll_seconds,
            "uses_real_calendar_time": True,
            "survives_restart": True,
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_task_id": self._last_task_id,
            "last_content_scan_at": (
                self._last_content_scan_at.isoformat() if self._last_content_scan_at else None
            ),
            "last_result": self._last_result,
            "last_reason": self._last_reason,
            "enqueued_tasks": self._enqueued_tasks,
            "held_tasks": self._held_tasks,
            "missed_tasks": self._missed_tasks,
            "reapproval_tasks": self._reapproval_tasks,
        }

    def public_snapshot(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.snapshot().items()
            if key not in {"last_task_id", "last_reason"}
        }

    def pause(self) -> bool:
        if not self._configured_enabled:
            return False
        return self._set_paused(True)

    def resume(self) -> bool:
        if not self._configured_enabled:
            return False
        return self._set_paused(False)

    def request_stop(self) -> None:
        self._stop_event.set()

    async def run_once(self) -> ProactiveWorkerRunResult:
        if not self.active:
            return ProactiveWorkerRunResult(status="disabled", reason="worker_not_active")
        if self._run_lock.locked():
            return ProactiveWorkerRunResult(status="busy", reason="worker_already_running")
        async with self._run_lock:
            now = self._now()
            self._last_started_at = now.isoformat()
            self._last_finished_at = None
            task = await self._repository.claim_due_proactive_task(
                now=now,
                lease_seconds=self._lease_seconds,
            )
            if task is None:
                result = ProactiveWorkerRunResult(status="idle", reason="no_due_task")
            else:
                self._last_task_id = task["id"]
                try:
                    result = await self._evaluate(task, now)
                except Exception as exc:
                    await self._repository.hold_proactive_task(
                        task["id"],
                        status=ProactiveTaskStatus.POLICY_BLOCKED,
                        reason=f"evaluation_error:{type(exc).__name__}",
                        next_eligible_at=now + timedelta(seconds=self._policy_recheck_seconds),
                        now=now,
                    )
                    result = ProactiveWorkerRunResult(
                        status="failed",
                        task_id=task["id"],
                        reason=type(exc).__name__,
                    )
            content_scan_due = (
                self._last_content_scan_at is None
                or now - self._last_content_scan_at
                >= timedelta(seconds=self._policy_recheck_seconds)
            )
            if self._content_composer is not None and content_scan_due:
                self._last_content_scan_at = now
                try:
                    composed = await self._content_composer.run_daily(now)
                    if result.status == "idle" and composed:
                        result = ProactiveWorkerRunResult("composed", reason=f"auto:{composed}")
                except Exception as exc:
                    if result.status == "idle":
                        result = ProactiveWorkerRunResult("failed", reason=type(exc).__name__)
            return self._finish(result)

    async def _evaluate(
        self,
        task: dict[str, Any],
        now: datetime,
    ) -> ProactiveWorkerRunResult:
        task_id = task["id"]
        if not task["policy_enabled"]:
            self._held_tasks += 1
            await self._repository.hold_proactive_task(
                task_id,
                status=ProactiveTaskStatus.POLICY_BLOCKED,
                reason="user_policy_disabled",
                next_eligible_at=now + timedelta(seconds=self._policy_recheck_seconds),
                now=now,
            )
            return ProactiveWorkerRunResult("held", task_id, "user_policy_disabled")

        owner_exception = task.get("hold_reason") == "owner_reapproved_exception"
        scheduled_for = _as_utc(task["original_scheduled_for"])
        lateness_seconds = max(0, int((now - scheduled_for).total_seconds()))
        should_check_lateness = (
            not task["was_previously_evaluated"]
            or task["status"] == ProactiveTaskStatus.POLICY_BLOCKED.value
        )
        if (
            not owner_exception
            and should_check_lateness
            and lateness_seconds > self._schedule_tolerance_seconds
        ):
            missed_policy = MissedTaskPolicy(task["missed_policy"])
            if missed_policy is MissedTaskPolicy.SKIP:
                return await self._mark_missed(task_id, now, "late_start_skip_policy")
            if missed_policy is MissedTaskPolicy.SEND_WITHIN_GRACE and lateness_seconds > int(
                task["missed_grace_seconds"]
            ):
                return await self._mark_missed(task_id, now, "missed_grace_window")
            if missed_policy is MissedTaskPolicy.REQUIRE_REAPPROVAL:
                return await self._request_reapproval(task_id, now, "late_start")

        if task["quiet_hours_enabled"] and not owner_exception:
            quiet = quiet_hours_decision(
                now,
                timezone_name=task["policy_timezone"],
                quiet_start=task["quiet_start"],
                quiet_end=task["quiet_end"],
            )
            if quiet.quiet:
                quiet_behavior = QuietHoursBehavior(task["quiet_behavior"])
                if quiet_behavior is QuietHoursBehavior.REQUIRE_REAPPROVAL:
                    return await self._request_reapproval(task_id, now, "quiet_hours")
                self._held_tasks += 1
                await self._repository.hold_proactive_task(
                    task_id,
                    status=ProactiveTaskStatus.WAITING_QUIET_HOURS,
                    reason="quiet_hours",
                    next_eligible_at=quiet.next_allowed_at or now,
                    now=now,
                )
                return ProactiveWorkerRunResult("held", task_id, "quiet_hours")

        day_start, day_end = local_day_bounds(now, task["policy_timezone"])
        window = await self._repository.proactive_delivery_window(
            target_qq=task["target_qq"],
            day_start=day_start,
            day_end=day_end,
        )
        if int(window["delivered_count"]) >= int(task["daily_limit"]):
            self._held_tasks += 1
            await self._repository.hold_proactive_task(
                task_id,
                status=ProactiveTaskStatus.WAITING_RATE_LIMIT,
                reason="daily_limit",
                next_eligible_at=day_end,
                now=now,
            )
            return ProactiveWorkerRunResult("held", task_id, "daily_limit")

        last_enqueued = window.get("last_enqueued_at")
        if last_enqueued:
            next_interval = _as_utc(last_enqueued) + timedelta(
                seconds=int(task["minimum_interval_seconds"])
            )
            if next_interval > now:
                self._held_tasks += 1
                await self._repository.hold_proactive_task(
                    task_id,
                    status=ProactiveTaskStatus.WAITING_RATE_LIMIT,
                    reason="minimum_interval",
                    next_eligible_at=next_interval,
                    now=now,
                )
                return ProactiveWorkerRunResult("held", task_id, "minimum_interval")

        if not await self._repository.enqueue_proactive_task(task_id, now=now):
            return ProactiveWorkerRunResult("conflict", task_id, "task_state_changed")
        self._enqueued_tasks += 1
        return ProactiveWorkerRunResult("enqueued", task_id)

    async def _mark_missed(
        self,
        task_id: str,
        now: datetime,
        reason: str,
    ) -> ProactiveWorkerRunResult:
        await self._repository.mark_proactive_missed(task_id, reason=reason, now=now)
        self._missed_tasks += 1
        return ProactiveWorkerRunResult("missed", task_id, reason)

    async def _request_reapproval(
        self,
        task_id: str,
        now: datetime,
        reason: str,
    ) -> ProactiveWorkerRunResult:
        approval_id = str(uuid4())
        approval_code = secrets.token_hex(3).upper()
        await self._repository.require_proactive_reapproval(
            task_id,
            reason=reason,
            approval_id=approval_id,
            approval_code=approval_code,
            requested_to=self._owner_qq,
            now=now,
        )
        self._reapproval_tasks += 1
        return ProactiveWorkerRunResult("reapproval_required", task_id, reason)

    def _finish(self, result: ProactiveWorkerRunResult) -> ProactiveWorkerRunResult:
        self._last_result = result.status
        self._last_reason = result.reason
        self._last_task_id = result.task_id
        self._last_finished_at = self._now().isoformat()
        return result

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("proactive worker loop is already running")
        self._loop_running = True
        self._stop_event.clear()
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_result = "loop_error"
                    self._last_reason = type(exc).__name__
                    self._last_finished_at = self._now().isoformat()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
