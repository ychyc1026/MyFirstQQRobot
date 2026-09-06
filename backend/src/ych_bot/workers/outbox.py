"""Explicitly invoked outbox delivery; disabled by default."""

from __future__ import annotations

import asyncio
from typing import Any

from ych_bot.application.readiness_guard import ReadinessBlockedError, ReadinessGuard
from ych_bot.application.reply_delivery import ReplyDeliveryService
from ych_bot.domain.errors import NapCatDeliveryUnknownError, NapCatRequestError
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient
from ych_bot.workers.runtime import DurablePauseMixin


class OutboxDispatcher(DurablePauseMixin):
    def __init__(
        self,
        repository: SQLiteRepository,
        client: NapCatClient,
        *,
        enabled: bool,
        readiness_guard: ReadinessGuard,
        poll_seconds: float = 1,
        max_attempts: int = 5,
        retry_base_seconds: int = 30,
        default_bot_qq: str = "",
    ) -> None:
        if not (0.1 <= poll_seconds <= 300):
            raise ValueError("outbox poll interval must be between 0.1 and 300 seconds")
        if not (1 <= max_attempts <= 20):
            raise ValueError("outbox max attempts must be between 1 and 20")
        if not (1 <= retry_base_seconds <= 3600):
            raise ValueError("outbox retry base must be between 1 and 3600 seconds")
        self.repository = repository
        self.client = client
        self.enabled = enabled
        self._readiness_guard = readiness_guard
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.default_bot_qq = default_bot_qq
        self._reply_delivery = ReplyDeliveryService(repository)
        self._pause_worker_name = "outbox"
        self._paused = False
        self._pause_state_loaded = False
        self._loop_running = False
        self._stop_event = asyncio.Event()
        self._sent_count = 0
        self._failed_count = 0
        self._recovered_count = 0
        self._last_result: str | None = None

    @property
    def active(self) -> bool:
        self._ensure_pause_state()
        return self.enabled and not self._paused

    def snapshot(self) -> dict[str, Any]:
        self._ensure_pause_state()
        return {
            "configured_enabled": self.enabled,
            "active": self.active,
            "paused": self._paused,
            "loop_running": self._loop_running,
            "poll_seconds": self.poll_seconds,
            "max_attempts": self.max_attempts,
            "retry_base_seconds": self.retry_base_seconds,
            "sent_count": self._sent_count,
            "failed_count": self._failed_count,
            "recovered_after_restart": self._recovered_count,
            "last_result": self._last_result,
        }

    def pause(self) -> bool:
        if not self.enabled:
            return False
        return self._set_paused(True)

    def resume(self) -> bool:
        if not self.enabled:
            return False
        return self._set_paused(False)

    def request_stop(self) -> None:
        self._stop_event.set()

    async def run_once(self) -> int:
        if not self.active:
            self._last_result = "disabled"
            return 0
        message = await self.repository.claim_outbound(max_attempts=self.max_attempts)
        if message is None:
            self._last_result = "idle"
            return 0
        reply_attempt = await self._reply_delivery.begin(message)
        effect_scope = await self.repository.outbound_effect_scope(message.id)
        try:
            await self._readiness_guard.require(
                effect_scope,
                operation="napcat.send_message",
                operation_id=message.id,
            )
            response = await self.client.send_message(message)
        except ReadinessBlockedError as exc:
            reason = f"readiness_{exc.blocker_codes[0]}"
            if reply_attempt is not None:
                await self._reply_delivery.finish(
                    message,
                    outcome="rejected",
                    safe_detail="reply blocked before NapCat delivery by current readiness",
                )
            else:
                await self.repository.mark_outbound_readiness_blocked(message.id, reason)
            self._failed_count += 1
            self._last_result = "readiness_blocked"
            return 0
        except NapCatRequestError:
            if reply_attempt is not None:
                await self._reply_delivery.finish(
                    message,
                    outcome="rejected",
                    safe_detail="NapCat confirmed that the reply was rejected",
                )
                self._failed_count += 1
                self._last_result = "rejected"
                return 0
            await self.repository.mark_outbound_failed(
                message.id,
                "NapCat confirmed that the outbound action was rejected",
                retry_base_seconds=self.retry_base_seconds,
            )
            self._failed_count += 1
            self._last_result = "failed"
            return 0
        except (NapCatDeliveryUnknownError, TimeoutError, ConnectionError):
            if reply_attempt is not None:
                await self._reply_delivery.finish(
                    message,
                    outcome="delivery_unknown",
                    safe_detail="reply delivery could not be confirmed",
                )
                self._failed_count += 1
                self._last_result = "delivery_unknown"
                return 0
            await self.repository.mark_outbound_failed(
                message.id,
                "outbound delivery could not be confirmed",
                retry_base_seconds=self.retry_base_seconds,
            )
            self._failed_count += 1
            self._last_result = "failed"
            return 0
        except Exception as error:
            if reply_attempt is not None:
                await self._reply_delivery.finish(
                    message,
                    outcome="delivery_unknown",
                    safe_detail="reply delivery ended without a confirmed outcome",
                )
                self._failed_count += 1
                self._last_result = "delivery_unknown"
                return 0
            await self.repository.mark_outbound_failed(
                message.id,
                str(error),
                retry_base_seconds=self.retry_base_seconds,
            )
            self._failed_count += 1
            self._last_result = "failed"
            return 0
        if reply_attempt is not None:
            data = response.get("data") if isinstance(response, dict) else None
            provider_message_id = (
                str(data.get("message_id"))
                if isinstance(data, dict) and data.get("message_id") is not None
                else None
            )
            await self._reply_delivery.finish(
                message,
                outcome="delivered",
                safe_detail="NapCat confirmed reply delivery",
                provider_message_id=provider_message_id,
            )
        await self.repository.mark_outbound_sent(message.id, bot_qq=self.default_bot_qq)
        self._sent_count += 1
        self._last_result = "sent"
        return 1

    async def run_forever(self) -> None:
        if self._loop_running:
            raise RuntimeError("outbox dispatcher loop is already running")
        if not self.enabled:
            return
        self._loop_running = True
        self._stop_event.clear()
        try:
            self._recovered_count += await self.repository.recover_sending_outbound()
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._failed_count += 1
                    self._last_result = "loop_error"
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._loop_running = False
