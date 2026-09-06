"""Default-off worker that enqueues owner reports into durable outbox."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.reports import ReportDeliveryPolicy, ReportDeliveryStatus
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.runtime import DurablePauseMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OwnerReportWorkerRunResult:
    status: str
    report_id: str | None = None
    reason: str | None = None


class OwnerReportWorker(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        owner_qq: str,
        configured_enabled: bool,
        reports_enabled: bool,
        poll_seconds: float,
        lease_seconds: int = 60,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not (0.1 <= poll_seconds <= 300):
            raise ValueError("owner report worker poll interval must be between 0.1 and 300")
        if not (10 <= lease_seconds <= 3600):
            raise ValueError("owner report worker lease must be between 10 and 3600 seconds")
        self._repository = repository
        self._owner_qq = owner_qq
        self._configured_enabled = configured_enabled
        self._reports_enabled = reports_enabled
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._pause_worker_name = "owner_reports"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._queued_count = 0
        self._delivered_count = 0
        self._failed_count = 0
        self._suppressed_count = 0
        self._last_result: str | None = None
        self._last_reason: str | None = None
        self._last_report_id: str | None = None

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
            raise ValueError("owner report worker clock must return an aware datetime")
        return value.astimezone(UTC)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self._configured_enabled,
            "delivery_route_enabled": self._reports_enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "poll_seconds": self._poll_seconds,
            "automatic_retry": False,
            "queued_count": self._queued_count,
            "delivered_count": self._delivered_count,
            "failed_count": self._failed_count,
            "suppressed_count": self._suppressed_count,
            "last_result": self._last_result,
            "last_reason": self._last_reason,
            "last_report_id": self._last_report_id,
        }

    def public_snapshot(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.snapshot().items()
            if key not in {"last_report_id", "last_reason"}
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

    def _finish(self, result: OwnerReportWorkerRunResult) -> OwnerReportWorkerRunResult:
        self._last_result = result.status
        self._last_reason = result.reason
        self._last_report_id = result.report_id
        return result

    async def run_once(self) -> OwnerReportWorkerRunResult:
        if not self.active:
            return self._finish(OwnerReportWorkerRunResult("disabled", reason="worker_not_active"))
        if self._run_lock.locked():
            return OwnerReportWorkerRunResult("busy", reason="worker_already_running")
        async with self._run_lock:
            now = self._now()
            report = await self._repository.claim_due_owner_report(
                now=now,
                lease_seconds=self._lease_seconds,
            )
            if report is None:
                return self._finish(OwnerReportWorkerRunResult("idle", reason="no_due_report"))
            return self._finish(await self._evaluate(report, now))

    async def _evaluate(
        self,
        report: dict[str, Any],
        now: datetime,
    ) -> OwnerReportWorkerRunResult:
        report_id = report["id"]
        delivery_status = report.get("delivery_status")
        if delivery_status == ReportDeliveryStatus.QUEUED.value:
            outbox_status = report.get("outbox_status")
            if outbox_status == "sent":
                await self._repository.mark_owner_report_delivered(report_id, now=now)
                self._delivered_count += 1
                return OwnerReportWorkerRunResult("delivered", report_id, "outbox_sent")
            if outbox_status == "failed":
                await self._repository.mark_owner_report_failed(
                    report_id,
                    error=str(report.get("last_error") or "outbox_failed"),
                    now=now,
                )
                self._failed_count += 1
                return OwnerReportWorkerRunResult("failed", report_id, "outbox_failed")
            if outbox_status == "rejected":
                await self._repository.mark_owner_report_failed(
                    report_id,
                    error="readiness_blocked",
                    now=now,
                )
                self._failed_count += 1
                return OwnerReportWorkerRunResult("failed", report_id, "readiness_blocked")
            return OwnerReportWorkerRunResult("waiting_outbox", report_id, outbox_status)

        if report.get("delivery_policy") == ReportDeliveryPolicy.DASHBOARD_ONLY.value:
            await self._repository.mark_owner_report_suppressed(
                report_id,
                reason="dashboard_only",
                now=now,
            )
            self._suppressed_count += 1
            return OwnerReportWorkerRunResult("suppressed", report_id, "dashboard_only")

        if not self._reports_enabled:
            return OwnerReportWorkerRunResult("disabled", report_id, "delivery_route_disabled")

        outbox_id = await self._repository.enqueue_owner_report_outbox(
            report_id=report_id,
            owner_qq=self._owner_qq,
            title=report["title"],
            body=report["body"],
            occurrence_count=int(report.get("occurrence_count") or 1),
            now=now,
        )
        if not outbox_id:
            await self._repository.mark_owner_report_failed(
                report_id,
                error="outbox_enqueue_conflict",
                now=now,
            )
            self._failed_count += 1
            return OwnerReportWorkerRunResult("failed", report_id, "outbox_enqueue_conflict")
        await self._repository.mark_owner_report_queued(
            report_id,
            outbox_id=outbox_id,
            now=now,
        )
        self._queued_count += 1
        return OwnerReportWorkerRunResult("queued", report_id, "enqueued")

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("owner report worker loop is already running")
        if not self._configured_enabled:
            return
        self._loop_running = True
        self._stop_event.clear()
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._last_result = "loop_error"
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False
