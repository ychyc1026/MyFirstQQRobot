"""Default-off polling worker for recoverable knowledge-processing jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ych_bot.application.knowledge_processing import KnowledgeProcessingService
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.runtime import DurablePauseMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class KnowledgeWorkerRunResult:
    status: str
    job_id: str | None = None
    reason: str | None = None


class KnowledgeProcessingWorker(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        service: KnowledgeProcessingService,
        *,
        configured_enabled: bool,
        poll_seconds: float,
        max_attempts: int,
        retry_base_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if poll_seconds < 0.1:
            raise ValueError("knowledge worker poll interval must be at least 0.1 seconds")
        if not (1 <= max_attempts <= 20):
            raise ValueError("knowledge worker max attempts must be between 1 and 20")
        if not (1 <= retry_base_seconds <= 86_400):
            raise ValueError("knowledge worker retry base must be between 1 and 86400 seconds")
        self._repository = repository
        self._service = service
        self._configured_enabled = configured_enabled
        self._poll_seconds = poll_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._clock = clock
        self._pause_worker_name = "knowledge"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_job_id: str | None = None
        self._last_result: str | None = None
        self._last_error_type: str | None = None
        self._next_retry_at: str | None = None
        self._completed_jobs = 0
        self._failed_attempts = 0
        self._exhausted_jobs = 0

    @property
    def configured_enabled(self) -> bool:
        return self._configured_enabled

    @property
    def active(self) -> bool:
        self._ensure_pause_state()
        return self._configured_enabled and self._service.enabled and not self._paused

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("knowledge worker clock must return an aware datetime")
        return value.astimezone(UTC)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self._configured_enabled,
            "processing_route_enabled": self._service.enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "currently_processing": self._run_lock.locked(),
            "poll_seconds": self._poll_seconds,
            "max_attempts": self._max_attempts,
            "retry_base_seconds": self._retry_base_seconds,
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_job_id": self._last_job_id,
            "last_result": self._last_result,
            "last_error_type": self._last_error_type,
            "next_retry_at": self._next_retry_at,
            "completed_jobs": self._completed_jobs,
            "failed_attempts": self._failed_attempts,
            "exhausted_jobs": self._exhausted_jobs,
        }

    def public_snapshot(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.snapshot().items()
            if key
            not in {
                "last_job_id",
                "last_error_type",
            }
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

    async def run_once(self) -> KnowledgeWorkerRunResult:
        if not self.active:
            return KnowledgeWorkerRunResult(status="disabled", reason="worker_not_active")
        if self._run_lock.locked():
            return KnowledgeWorkerRunResult(status="busy", reason="worker_already_running")
        async with self._run_lock:
            now = self._now()
            self._last_started_at = now.isoformat()
            self._last_finished_at = None
            self._next_retry_at = None
            self._exhausted_jobs = 0
            candidates = await self._repository.knowledge_worker_candidates(limit=500)
            selected: dict[str, Any] | None = None
            earliest_retry: datetime | None = None
            for candidate in candidates:
                attempt_count = int(candidate["attempt_count"])
                if attempt_count >= self._max_attempts:
                    self._exhausted_jobs += 1
                    continue
                lease_expires_at = _optional_datetime(candidate.get("lease_expires_at"))
                if (
                    candidate.get("checkpoint_state") == "processing"
                    and lease_expires_at is not None
                    and lease_expires_at > now
                ):
                    continue
                ready_at = self._retry_ready_at(candidate)
                if ready_at is not None and ready_at > now:
                    if earliest_retry is None or ready_at < earliest_retry:
                        earliest_retry = ready_at
                    continue
                selected = candidate
                break
            self._next_retry_at = earliest_retry.isoformat() if earliest_retry else None
            if selected is None:
                self._last_result = "idle"
                self._last_job_id = None
                self._last_error_type = None
                self._last_finished_at = self._now().isoformat()
                return KnowledgeWorkerRunResult(status="idle", reason="no_ready_job")

            job_id = selected["id"]
            self._last_job_id = job_id
            try:
                await self._service.process(job_id)
            except Exception as exc:
                self._failed_attempts += 1
                self._last_result = "failed"
                self._last_error_type = type(exc).__name__
                self._last_finished_at = self._now().isoformat()
                return KnowledgeWorkerRunResult(
                    status="failed",
                    job_id=job_id,
                    reason=type(exc).__name__,
                )
            self._completed_jobs += 1
            self._last_result = "completed"
            self._last_error_type = None
            self._last_finished_at = self._now().isoformat()
            return KnowledgeWorkerRunResult(status="completed", job_id=job_id)

    def _retry_ready_at(self, candidate: dict[str, Any]) -> datetime | None:
        if candidate.get("checkpoint_state") != "failed":
            return None
        updated_at = _optional_datetime(candidate.get("checkpoint_updated_at"))
        if updated_at is None:
            return None
        attempt_count = max(1, int(candidate["attempt_count"]))
        delay = min(self._retry_base_seconds * (2 ** (attempt_count - 1)), 86_400)
        return updated_at + timedelta(seconds=delay)

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("knowledge worker loop is already running")
        self._loop_running = True
        self._stop_event.clear()
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._failed_attempts += 1
                    self._last_result = "loop_error"
                    self._last_error_type = type(exc).__name__
                    self._last_finished_at = self._now().isoformat()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
