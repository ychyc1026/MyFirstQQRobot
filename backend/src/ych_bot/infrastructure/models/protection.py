"""Persistent request budgets and circuit breakers for model gateways."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ChatModelGateway,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageModelGateway,
    ModelBudgetExceededError,
    ModelCallConflictError,
    ModelCircuitOpenError,
    ModelUnavailableError,
)
from ych_bot.domain.readiness import CapabilityScope
from ych_bot.infrastructure.database import SQLiteRepository


@dataclass(frozen=True, slots=True)
class ModelProtectionPolicy:
    route: str
    timezone: str
    daily_request_limit: int
    daily_token_limit: int | None
    failure_threshold: int
    cooldown_seconds: int
    lease_seconds: int

    def __post_init__(self) -> None:
        if self.route not in {"chat", "image", "vision", "stats"}:
            raise ValueError("model protection route must be chat, image, vision, or stats")
        ZoneInfo(self.timezone)
        if self.daily_request_limit < 1:
            raise ValueError("daily model request limit must be positive")
        if self.daily_token_limit is not None and self.daily_token_limit < 1:
            raise ValueError("daily model token limit must be positive")
        if self.failure_threshold < 1:
            raise ValueError("model failure threshold must be positive")
        if self.cooldown_seconds < 1:
            raise ValueError("model cooldown must be positive")
        if self.lease_seconds < 1:
            raise ValueError("model call lease must be positive")


@dataclass(frozen=True, slots=True)
class ModelProtectionSnapshot:
    route: str
    day_key: str
    state: str
    requests_used: int
    request_limit: int
    requests_rejected: int
    tokens_used: int
    token_limit: int | None
    consecutive_failures: int
    failure_threshold: int
    active_calls: int
    last_failure_at: str | None
    open_until: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "day_key": self.day_key,
            "state": self.state,
            "requests_used": self.requests_used,
            "request_limit": self.request_limit,
            "requests_rejected": self.requests_rejected,
            "tokens_used": self.tokens_used,
            "token_limit": self.token_limit,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "active_calls": self.active_calls,
            "last_failure_at": self.last_failure_at,
            "open_until": self.open_until,
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ModelCallGuard:
    def __init__(
        self,
        repository: SQLiteRepository,
        policy: ModelProtectionPolicy,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self.policy = policy
        self._clock = clock
        self._timezone = ZoneInfo(policy.timezone)

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("model protection clock must return an aware datetime")
        return current.astimezone(UTC)

    def _day_key(self, now: datetime) -> str:
        return now.astimezone(self._timezone).date().isoformat()

    async def acquire(self, *, request_id: str, reserved_tokens: int) -> None:
        if not request_id.strip():
            raise ValueError("model request_id must not be empty")
        if reserved_tokens < 0:
            raise ValueError("reserved model tokens must not be negative")
        now = self._now()
        decision = await self._repository.reserve_model_call(
            route=self.policy.route,
            request_id=request_id,
            day_key=self._day_key(now),
            now=now,
            lease_expires_at=now + timedelta(seconds=self.policy.lease_seconds),
            daily_request_limit=self.policy.daily_request_limit,
            daily_token_limit=self.policy.daily_token_limit,
            reserved_tokens=reserved_tokens,
            failure_threshold=self.policy.failure_threshold,
            cooldown_seconds=self.policy.cooldown_seconds,
        )
        if decision["allowed"]:
            return
        reason = decision["reason"]
        if reason in {"daily_request_budget", "daily_token_budget"}:
            raise ModelBudgetExceededError(f"{self.policy.route} model call rejected by {reason}")
        if reason in {"circuit_open", "circuit_half_open"}:
            open_until = decision.get("open_until") or "probe_in_progress"
            raise ModelCircuitOpenError(
                f"{self.policy.route} model circuit rejected the call until {open_until}"
            )
        if reason == "duplicate_request_id":
            raise ModelCallConflictError(
                f"duplicate {self.policy.route} model request_id was rejected"
            )
        raise ModelUnavailableError(f"{self.policy.route} model call was rejected locally")

    async def success(
        self,
        *,
        request_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        await self._repository.finish_model_call_success(
            route=self.policy.route,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            completed_at=self._now(),
        )

    async def failure(self, *, request_id: str, failure_type: str) -> None:
        await self._repository.finish_model_call_failure(
            route=self.policy.route,
            request_id=request_id,
            failure_type=failure_type,
            completed_at=self._now(),
        )

    async def snapshot(self) -> ModelProtectionSnapshot:
        now = self._now()
        raw = await self._repository.model_protection_snapshot(
            route=self.policy.route,
            day_key=self._day_key(now),
            now=now,
            failure_threshold=self.policy.failure_threshold,
            cooldown_seconds=self.policy.cooldown_seconds,
        )
        return ModelProtectionSnapshot(
            route=self.policy.route,
            day_key=self._day_key(now),
            state=raw["state"],
            requests_used=raw["requests_used"],
            request_limit=self.policy.daily_request_limit,
            requests_rejected=raw["requests_rejected"],
            tokens_used=raw["tokens_used"],
            token_limit=self.policy.daily_token_limit,
            consecutive_failures=raw["consecutive_failures"],
            failure_threshold=self.policy.failure_threshold,
            active_calls=raw["active_calls"],
            last_failure_at=raw["last_failure_at"],
            open_until=raw["open_until"],
        )


class ExternalReadinessGuard(Protocol):
    async def require(
        self,
        capability_scope: CapabilityScope,
        *,
        operation: str,
        operation_id: str = "",
    ) -> Any: ...


class ProtectedChatModelGateway:
    def __init__(
        self,
        gateway: ChatModelGateway,
        guard: ModelCallGuard,
        readiness_guard: ExternalReadinessGuard,
        *,
        capability_scope: CapabilityScope = CapabilityScope.CHAT_MODEL,
    ) -> None:
        if capability_scope not in {
            CapabilityScope.CHAT_MODEL,
            CapabilityScope.VISION_MODEL,
            CapabilityScope.STATS_MODEL,
        }:
            raise ValueError("chat gateway readiness scope must be chat, vision, or stats")
        self._gateway = gateway
        self._guard = guard
        self._readiness_guard = readiness_guard
        self._capability_scope = capability_scope

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        await self._readiness_guard.require(
            self._capability_scope,
            operation="model.generate",
            operation_id=request.request_id,
        )
        reserved_tokens = _estimate_chat_reservation(request)
        await self._guard.acquire(
            request_id=request.request_id,
            reserved_tokens=reserved_tokens,
        )
        try:
            result = await self._gateway.generate(request)
        except asyncio.CancelledError:
            await self._guard.failure(
                request_id=request.request_id,
                failure_type="CancelledError",
            )
            raise
        except Exception as exc:
            await self._guard.failure(
                request_id=request.request_id,
                failure_type=type(exc).__name__,
            )
            raise
        await self._guard.success(
            request_id=request.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return result

    async def close(self) -> None:
        await self._gateway.close()


class ProtectedImageModelGateway:
    def __init__(
        self,
        gateway: ImageModelGateway,
        guard: ModelCallGuard,
        readiness_guard: ExternalReadinessGuard,
    ) -> None:
        self._gateway = gateway
        self._guard = guard
        self._readiness_guard = readiness_guard

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        await self._readiness_guard.require(
            CapabilityScope.IMAGE_MODEL,
            operation="model.generate",
            operation_id=request.request_id,
        )
        await self._guard.acquire(request_id=request.request_id, reserved_tokens=0)
        try:
            result = await self._gateway.generate(request)
        except asyncio.CancelledError:
            await self._guard.failure(
                request_id=request.request_id,
                failure_type="CancelledError",
            )
            raise
        except Exception as exc:
            await self._guard.failure(
                request_id=request.request_id,
                failure_type=type(exc).__name__,
            )
            raise
        await self._guard.success(request_id=request.request_id)
        return result

    async def close(self) -> None:
        await self._gateway.close()


def _estimate_chat_reservation(request: ChatGenerationRequest) -> int:
    prompt_bytes = 0
    for message in request.messages:
        prompt_bytes += len(message.content.encode("utf-8"))
        prompt_bytes += sum(
            2048 if url.startswith("data:image/") else min(len(url.encode("utf-8")), 256) + 2048
            for url in message.image_urls
        )
    prompt_estimate = max(1, (prompt_bytes + 2) // 3)
    return prompt_estimate + max(0, request.max_output_tokens)
