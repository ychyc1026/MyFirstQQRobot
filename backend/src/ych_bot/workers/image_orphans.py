"""Default-off polling worker for image artifact orphan scans."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ych_bot.application.images import ImageGenerationService, ImageGenerationTaskError
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.runtime import DurablePauseMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ImageOrphanWorkerRunResult:
    status: str
    scan_id: str | None = None
    reason: str | None = None


class ImageOrphanWorker(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        service: ImageGenerationService,
        *,
        configured_enabled: bool,
        poll_seconds: float,
        owner_qq: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if poll_seconds < 0.1:
            raise ValueError("image orphan worker poll interval must be at least 0.1 seconds")
        self._repository = repository
        self._service = service
        self._configured_enabled = configured_enabled
        self._poll_seconds = poll_seconds
        self._owner_qq = owner_qq
        self._clock = clock
        self._pause_worker_name = "image_orphan"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_result: str | None = None
        self._last_error_type: str | None = None
        self._last_scan_id: str | None = None
        self._completed_scans = 0

    @property
    def configured_enabled(self) -> bool:
        return self._configured_enabled

    @property
    def active(self) -> bool:
        self._ensure_pause_state()
        return self._configured_enabled and self._service.orphan_scan_enabled and not self._paused

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("image orphan worker clock must return an aware datetime")
        return value.astimezone(UTC)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self._configured_enabled,
            "scan_enabled": self._service.orphan_scan_enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "currently_processing": self._run_lock.locked(),
            "poll_seconds": self._poll_seconds,
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_result": self._last_result,
            "last_error_type": self._last_error_type,
            "last_scan_id": self._last_scan_id,
            "completed_scans": self._completed_scans,
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

    async def run_once(self) -> ImageOrphanWorkerRunResult:
        if not self.active:
            return ImageOrphanWorkerRunResult(status="disabled", reason="worker_not_active")
        if self._run_lock.locked():
            return ImageOrphanWorkerRunResult(status="busy", reason="worker_already_running")
        async with self._run_lock:
            now = self._now()
            self._last_started_at = now.isoformat()
            self._last_finished_at = None
            try:
                scan = await self._service.scan_orphans(created_by=self._owner_qq)
            except ImageGenerationTaskError as exc:
                self._last_result = "failed"
                self._last_error_type = type(exc).__name__
                self._last_finished_at = self._now().isoformat()
                return ImageOrphanWorkerRunResult(status="failed", reason=str(exc))
            self._completed_scans += 1
            self._last_scan_id = str(scan["id"])
            self._last_result = "completed"
            self._last_error_type = None
            self._last_finished_at = self._now().isoformat()
            return ImageOrphanWorkerRunResult(status="completed", scan_id=str(scan["id"]))

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("image orphan worker loop is already running")
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
                    self._last_error_type = type(exc).__name__
                    self._last_finished_at = self._now().isoformat()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False
