"""Default-off QQ Zone publisher with no blind network retries."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ych_bot.application.readiness_guard import ReadinessBlockedError, ReadinessGuard
from ych_bot.domain.control import QzonePostStatus
from ych_bot.domain.proactive import (
    MissedTaskPolicy,
    QuietHoursBehavior,
    local_day_bounds,
    quiet_hours_decision,
)
from ych_bot.domain.readiness import CapabilityScope
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.runtime import DurablePauseMixin


class QzonePublisher(Protocol):
    async def publish_qzone(
        self,
        *,
        content: str,
        images: list[str] | None = None,
        visibility: int = 1,
        target_uins: list[str] | None = None,
    ) -> str: ...

    async def delete_qzone(self, tid: str) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class QzoneWorkerRunResult:
    status: str
    post_id: str | None = None
    reason: str | None = None


class QzonePublishWorker(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        publisher: QzonePublisher,
        *,
        owner_qq: str,
        configured_enabled: bool,
        publish_enabled: bool,
        readiness_guard: ReadinessGuard,
        poll_seconds: float,
        lease_seconds: int = 120,
        schedule_tolerance_seconds: int = 60,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not (0.1 <= poll_seconds <= 300):
            raise ValueError("qzone worker poll interval must be between 0.1 and 300 seconds")
        if not (30 <= lease_seconds <= 3600):
            raise ValueError("qzone worker lease must be between 30 and 3600 seconds")
        if not (0 <= schedule_tolerance_seconds <= 600):
            raise ValueError("qzone schedule tolerance must be between 0 and 600 seconds")
        self._repository = repository
        self._publisher = publisher
        self._owner_qq = owner_qq
        self._configured_enabled = configured_enabled
        self._publish_enabled = publish_enabled
        self._readiness_guard = readiness_guard
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._schedule_tolerance_seconds = schedule_tolerance_seconds
        self._clock = clock
        self._pause_worker_name = "qzone"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_post_id: str | None = None
        self._last_result: str | None = None
        self._last_reason: str | None = None
        self._published_count = 0
        self._held_count = 0
        self._uncertain_count = 0
        self._missed_count = 0
        self._deleted_count = 0
        self._delete_uncertain_count = 0

    @property
    def configured_enabled(self) -> bool:
        return self._configured_enabled

    @property
    def active(self) -> bool:
        self._ensure_pause_state()
        return self._configured_enabled and self._publish_enabled and not self._paused

    @property
    def loop_should_start(self) -> bool:
        return self._configured_enabled and self._publish_enabled

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("qzone worker clock must return an aware datetime")
        return value.astimezone(UTC)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self._configured_enabled,
            "publish_route_enabled": self._publish_enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "currently_evaluating": self._run_lock.locked(),
            "poll_seconds": self._poll_seconds,
            "automatic_network_retry": False,
            "last_post_id": self._last_post_id,
            "last_result": self._last_result,
            "last_reason": self._last_reason,
            "published_count": self._published_count,
            "held_count": self._held_count,
            "uncertain_count": self._uncertain_count,
            "missed_count": self._missed_count,
            "deleted_count": self._deleted_count,
            "delete_uncertain_count": self._delete_uncertain_count,
        }

    def public_snapshot(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.snapshot().items()
            if key not in {"last_post_id", "last_reason"}
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

    async def run_once(self) -> QzoneWorkerRunResult:
        if not self.active:
            return self._finish(QzoneWorkerRunResult("disabled", reason="worker_not_active"))
        if self._run_lock.locked():
            return QzoneWorkerRunResult("busy", reason="worker_already_running")
        async with self._run_lock:
            now = self._now()
            delete_post = await self._repository.claim_due_qzone_delete(
                now=now,
                lease_seconds=self._lease_seconds,
            )
            if delete_post is not None:
                self._last_post_id = delete_post["id"]
                try:
                    result = await self._delete(delete_post, now)
                except ReadinessBlockedError as exc:
                    reason = f"readiness_{exc.blocker_codes[0]}"
                    await self._repository.mark_qzone_readiness_blocked(
                        delete_post["id"],
                        expected_status=QzonePostStatus.DELETE_APPROVED,
                        reason=reason,
                        now=now,
                    )
                    result = QzoneWorkerRunResult("failed", delete_post["id"], reason)
                except Exception as exc:
                    await self._repository.mark_qzone_delete_uncertain(
                        delete_post["id"],
                        error_type=type(exc).__name__,
                        now=now,
                    )
                    self._delete_uncertain_count += 1
                    result = QzoneWorkerRunResult(
                        "delete_uncertain",
                        delete_post["id"],
                        type(exc).__name__,
                    )
                return self._finish(result)
            post = await self._repository.claim_due_qzone_post(
                now=now,
                lease_seconds=self._lease_seconds,
            )
            if post is None:
                return self._finish(QzoneWorkerRunResult("idle", reason="no_due_post"))
            self._last_post_id = post["id"]
            try:
                result = await self._evaluate(post, now)
            except ReadinessBlockedError as exc:
                reason = f"readiness_{exc.blocker_codes[0]}"
                await self._repository.mark_qzone_readiness_blocked(
                    post["id"],
                    expected_status=QzonePostStatus.EVALUATING,
                    reason=reason,
                    now=now,
                )
                result = QzoneWorkerRunResult("failed", post["id"], reason)
            except Exception as exc:
                await self._repository.mark_qzone_evaluation_failed(
                    post["id"],
                    error_type=type(exc).__name__,
                    now=now,
                )
                result = QzoneWorkerRunResult("failed", post["id"], type(exc).__name__)
            return self._finish(result)

    async def _delete(self, post: dict[str, Any], now: datetime) -> QzoneWorkerRunResult:
        post_id = post["id"]
        tid = str(post.get("qzone_tid") or "").strip()
        if not tid:
            return QzoneWorkerRunResult("failed", post_id, "missing_tid")
        await self._readiness_guard.require(
            CapabilityScope.QZONE_PUBLISH,
            operation="qzone.delete",
            operation_id=post_id,
        )
        if not await self._repository.begin_qzone_delete(post_id, now=now):
            return QzoneWorkerRunResult("conflict", post_id, "post_state_changed")
        try:
            await self._publisher.delete_qzone(tid)
        except Exception as exc:
            await self._repository.mark_qzone_delete_uncertain(
                post_id,
                error_type=type(exc).__name__,
                now=now,
            )
            self._delete_uncertain_count += 1
            return QzoneWorkerRunResult("delete_uncertain", post_id, type(exc).__name__)
        if not await self._repository.complete_qzone_delete(post_id, now=now):
            await self._repository.mark_qzone_delete_uncertain(
                post_id,
                error_type="delete_completion_conflict",
                now=now,
            )
            self._delete_uncertain_count += 1
            return QzoneWorkerRunResult("delete_uncertain", post_id, "delete_completion_conflict")
        self._deleted_count += 1
        return QzoneWorkerRunResult("deleted", post_id)

    async def _evaluate(self, post: dict[str, Any], now: datetime) -> QzoneWorkerRunResult:
        post_id = post["id"]
        owner_exception = post["owner_exception"]
        scheduled = _as_utc(post["original_scheduled_for"])
        lateness = max(0, int((now - scheduled).total_seconds()))
        if (
            not owner_exception
            and not post["was_previously_evaluated"]
            and lateness > self._schedule_tolerance_seconds
        ):
            policy = MissedTaskPolicy(post["missed_policy"])
            if policy is MissedTaskPolicy.SKIP:
                return await self._mark_missed(post_id, now, "late_start_skip_policy")
            if policy is MissedTaskPolicy.SEND_WITHIN_GRACE and lateness > int(
                post["missed_grace_seconds"]
            ):
                return await self._mark_missed(post_id, now, "missed_grace_window")
            if policy is MissedTaskPolicy.REQUIRE_REAPPROVAL:
                return await self._request_reapproval(post_id, now, "late_start")

        if post["quiet_hours_enabled"] and not owner_exception:
            quiet = quiet_hours_decision(
                now,
                timezone_name=post["policy_timezone"],
                quiet_start=post["quiet_start"],
                quiet_end=post["quiet_end"],
            )
            if quiet.quiet:
                quiet_behavior = QuietHoursBehavior(post["quiet_behavior"])
                if quiet_behavior is QuietHoursBehavior.REQUIRE_REAPPROVAL:
                    return await self._request_reapproval(post_id, now, "quiet_hours")
                await self._repository.hold_qzone_post(
                    post_id,
                    status=QzonePostStatus.WAITING_QUIET_HOURS,
                    reason="quiet_hours",
                    next_eligible_at=quiet.next_allowed_at or now,
                    now=now,
                )
                self._held_count += 1
                return QzoneWorkerRunResult("held", post_id, "quiet_hours")

        day_start, day_end = local_day_bounds(now, post["policy_timezone"])
        window = await self._repository.qzone_delivery_window(
            day_start=day_start,
            day_end=day_end,
        )
        if int(window["published_count"]) >= int(post["daily_limit"]):
            await self._repository.hold_qzone_post(
                post_id,
                status=QzonePostStatus.WAITING_RATE_LIMIT,
                reason="daily_limit",
                next_eligible_at=day_end,
                now=now,
            )
            self._held_count += 1
            return QzoneWorkerRunResult("held", post_id, "daily_limit")
        last_published = window.get("last_published_at")
        if last_published:
            next_allowed = _as_utc(last_published) + timedelta(
                seconds=int(post["minimum_interval_seconds"])
            )
            if next_allowed > now:
                await self._repository.hold_qzone_post(
                    post_id,
                    status=QzonePostStatus.WAITING_RATE_LIMIT,
                    reason="minimum_interval",
                    next_eligible_at=next_allowed,
                    now=now,
                )
                self._held_count += 1
                return QzoneWorkerRunResult("held", post_id, "minimum_interval")

        await self._readiness_guard.require(
            CapabilityScope.QZONE_PUBLISH,
            operation="qzone.publish",
            operation_id=post_id,
        )
        if not await self._repository.begin_qzone_publish(post_id, now=now):
            return QzoneWorkerRunResult("conflict", post_id, "post_state_changed")
        try:
            tid = await self._publisher.publish_qzone(
                content=post["content"],
                images=post["images"] or None,
                visibility=int(post["visibility"]),
                target_uins=post["target_uins"] or None,
            )
        except Exception as exc:
            await self._repository.mark_qzone_publish_uncertain(
                post_id,
                error_type=type(exc).__name__,
                now=now,
            )
            self._uncertain_count += 1
            return QzoneWorkerRunResult("uncertain", post_id, type(exc).__name__)
        try:
            completed = await self._repository.complete_qzone_publish(
                post_id,
                qzone_tid=tid,
                now=now,
            )
        except Exception as exc:
            await self._repository.mark_qzone_publish_uncertain(
                post_id,
                error_type=f"completion_{type(exc).__name__}",
                now=now,
            )
            self._uncertain_count += 1
            return QzoneWorkerRunResult(
                "uncertain",
                post_id,
                f"completion_{type(exc).__name__}",
            )
        if not completed:
            await self._repository.mark_qzone_publish_uncertain(
                post_id,
                error_type="publish_completion_conflict",
                now=now,
            )
            self._uncertain_count += 1
            return QzoneWorkerRunResult("uncertain", post_id, "publish_completion_conflict")
        self._published_count += 1
        return QzoneWorkerRunResult("published", post_id)

    async def _mark_missed(
        self,
        post_id: str,
        now: datetime,
        reason: str,
    ) -> QzoneWorkerRunResult:
        await self._repository.mark_qzone_missed(post_id, reason=reason, now=now)
        self._missed_count += 1
        return QzoneWorkerRunResult("missed", post_id, reason)

    async def _request_reapproval(
        self,
        post_id: str,
        now: datetime,
        reason: str,
    ) -> QzoneWorkerRunResult:
        await self._repository.require_qzone_reapproval(
            post_id,
            reason=reason,
            approval_id=str(uuid4()),
            approval_code=secrets.token_hex(3).upper(),
            requested_to=self._owner_qq,
            now=now,
        )
        return QzoneWorkerRunResult("reapproval_required", post_id, reason)

    def _finish(self, result: QzoneWorkerRunResult) -> QzoneWorkerRunResult:
        self._last_post_id = result.post_id
        self._last_result = result.status
        self._last_reason = result.reason
        return result

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("qzone worker loop is already running")
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
