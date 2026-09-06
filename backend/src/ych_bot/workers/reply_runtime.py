"""Supervised durable production reply worker."""

from __future__ import annotations

import asyncio
from typing import Any

from ych_bot.domain.reply_pipeline import ReplyRuntimeLifecycle


class ReplyRuntimeWorker:
    def __init__(
        self,
        repository: Any,
        runtime: Any,
        *,
        bot_qq: str,
        configured_enabled: bool = False,
        poll_seconds: float = 1,
        worker_id: str = "reply-runtime",
    ) -> None:
        if not 0.1 <= poll_seconds <= 300:
            raise ValueError("reply worker poll interval must be between 0.1 and 300 seconds")
        self.repository = repository
        self.runtime = runtime
        self.bot_qq = bot_qq
        self.configured_enabled = configured_enabled
        self.poll_seconds = poll_seconds
        self.worker_id = worker_id
        self._loop_running = False
        self._stop_event = asyncio.Event()
        self._last_result: str | None = None

    @property
    def loop_should_start(self) -> bool:
        return self.configured_enabled

    def request_stop(self) -> None:
        self._stop_event.set()

    async def snapshot(self) -> dict[str, Any]:
        status = await self.runtime.control.status()
        state = status["state"]
        return {
            "configured_enabled": self.configured_enabled,
            "loop_running": self._loop_running,
            "requested_mode": status["requested_mode"],
            "effective_mode": status["effective_mode"],
            "paused": bool(state["emergency_paused"]),
            "lifecycle": state["lifecycle"],
            "last_iteration_at": state["last_iteration_at"],
            "last_claim_at": state["last_claim_at"],
            "last_progress_at": state["last_progress_at"],
            "last_failure_code": state["last_failure_code"],
            "recovered_count": int(state["recovered_count"]),
            "last_result": self._last_result,
            "blockers": status["blockers"],
        }

    async def run_once(self) -> int:
        if not self.configured_enabled:
            self._last_result = "disabled"
            return 0
        await self.repository.update_reply_worker_health(
            bot_qq=self.bot_qq, lifecycle=ReplyRuntimeLifecycle.RUNNING
        )
        try:
            result = await self.runtime.run_once(worker_id=self.worker_id)
        except Exception:
            self._last_result = "worker_failed"
            await self.repository.update_reply_worker_health(
                bot_qq=self.bot_qq,
                lifecycle=ReplyRuntimeLifecycle.FAILED,
                failure_code="worker_failed",
            )
            await self.repository.record_reply_runtime_event(
                bot_qq=self.bot_qq,
                event_type="worker_failed",
                actor=self.worker_id,
                source="worker",
                details={"failure_code": "worker_failed"},
            )
            raise
        progressed = result.run_id is not None and result.status not in {"idle", "paused"}
        self._last_result = result.status
        await self.repository.update_reply_worker_health(
            bot_qq=self.bot_qq,
            lifecycle=(
                ReplyRuntimeLifecycle.PAUSED
                if result.status == "paused"
                else ReplyRuntimeLifecycle.IDLE
            ),
            claimed=result.run_id is not None,
            progressed=progressed,
            recovered=bool(getattr(result, "recovered_lease", False)),
        )
        return int(progressed)

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("reply runtime loop is already running")
        if not self.configured_enabled:
            return
        self._loop_running = True
        self._stop_event.clear()
        await self.repository.update_reply_worker_health(
            bot_qq=self.bot_qq, lifecycle=ReplyRuntimeLifecycle.IDLE
        )
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False
            lifecycle = (
                ReplyRuntimeLifecycle.FAILED
                if self._last_result == "worker_failed"
                else ReplyRuntimeLifecycle.DISABLED
            )
            await self.repository.update_reply_worker_health(
                bot_qq=self.bot_qq,
                lifecycle=lifecycle,
                failure_code=(
                    "worker_failed" if lifecycle is ReplyRuntimeLifecycle.FAILED else None
                ),
            )
