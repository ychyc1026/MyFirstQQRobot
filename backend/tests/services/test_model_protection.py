from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _support.readiness import ALLOW_READINESS
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ImageGenerationRequest,
    ImageGenerationResult,
    ModelBudgetExceededError,
    ModelCircuitOpenError,
    ModelMessage,
    ModelRole,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import (
    ModelCallGuard,
    ModelProtectionPolicy,
    ProtectedChatModelGateway,
    ProtectedImageModelGateway,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeChatGateway:
    def __init__(self, outcomes: list[ChatGenerationResult | Exception] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[str] = []
        self.closed = False

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.calls.append(request.request_id)
        outcome = self.outcomes.pop(0) if self.outcomes else ChatGenerationResult(text="ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class FakeImageGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls.append(request.request_id)
        return ImageGenerationResult(artifacts=("data:image/png;base64,YWJj",))

    async def close(self) -> None:
        return None


def chat_request(request_id: str, *, max_output_tokens: int = 10) -> ChatGenerationRequest:
    return ChatGenerationRequest(
        request_id=request_id,
        messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
        max_output_tokens=max_output_tokens,
    )


def chat_policy(
    *,
    request_limit: int = 10,
    token_limit: int = 10_000,
    failure_threshold: int = 3,
    cooldown_seconds: int = 60,
) -> ModelProtectionPolicy:
    return ModelProtectionPolicy(
        route="chat",
        timezone="Asia/Shanghai",
        daily_request_limit=request_limit,
        daily_token_limit=token_limit,
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
        lease_seconds=300,
    )


@pytest.mark.asyncio
async def test_daily_request_budget_persists_across_guard_instances(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "budget.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, 2, tzinfo=UTC))
    provider = FakeChatGateway(
        [
            ChatGenerationResult(text="one", input_tokens=2, output_tokens=1),
            ChatGenerationResult(text="two", input_tokens=2, output_tokens=1),
        ]
    )
    first = ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, chat_policy(request_limit=2), clock=clock),
        ALLOW_READINESS,
    )

    await first.generate(chat_request("one"))
    await first.generate(chat_request("two"))

    restarted_guard = ModelCallGuard(repository, chat_policy(request_limit=2), clock=clock)
    restarted = ProtectedChatModelGateway(provider, restarted_guard, ALLOW_READINESS)
    with pytest.raises(ModelBudgetExceededError, match="daily_request_budget"):
        await restarted.generate(chat_request("three"))

    snapshot = await restarted_guard.snapshot()
    assert provider.calls == ["one", "two"]
    assert snapshot.requests_used == 2
    assert snapshot.requests_rejected == 1
    assert snapshot.day_key == "2026-08-13"
    assert (await repository.counts())["model_call_events"] == 3


@pytest.mark.asyncio
async def test_token_budget_rejects_before_provider_call(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tokens.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, tzinfo=UTC))
    provider = FakeChatGateway()
    guard = ModelCallGuard(repository, chat_policy(token_limit=5), clock=clock)
    gateway = ProtectedChatModelGateway(provider, guard, ALLOW_READINESS)

    with pytest.raises(ModelBudgetExceededError, match="daily_token_budget"):
        await gateway.generate(chat_request("too-large", max_output_tokens=10))

    assert provider.calls == []
    snapshot = await guard.snapshot()
    assert snapshot.requests_used == 0
    assert snapshot.requests_rejected == 1
    assert snapshot.tokens_used == 0


@pytest.mark.asyncio
async def test_circuit_opens_persists_and_successful_probe_resets_it(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "circuit.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, tzinfo=UTC))
    provider = FakeChatGateway(
        [RuntimeError("first"), RuntimeError("second"), ChatGenerationResult(text="recovered")]
    )
    policy = chat_policy(failure_threshold=2, cooldown_seconds=60)
    gateway = ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, policy, clock=clock),
        ALLOW_READINESS,
    )

    with pytest.raises(RuntimeError, match="first"):
        await gateway.generate(chat_request("failure-one"))
    with pytest.raises(RuntimeError, match="second"):
        await gateway.generate(chat_request("failure-two"))

    restarted_guard = ModelCallGuard(repository, policy, clock=clock)
    restarted = ProtectedChatModelGateway(provider, restarted_guard, ALLOW_READINESS)
    with pytest.raises(ModelCircuitOpenError, match="circuit rejected"):
        await restarted.generate(chat_request("blocked"))
    assert provider.calls == ["failure-one", "failure-two"]
    assert (await restarted_guard.snapshot()).state == "open"

    clock.advance(seconds=61)
    assert (await restarted_guard.snapshot()).state == "half_open_ready"
    recovered = await restarted.generate(chat_request("probe"))

    assert recovered.text == "recovered"
    snapshot = await restarted_guard.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.consecutive_failures == 0


@pytest.mark.asyncio
async def test_image_budget_is_independent_and_has_no_token_limit(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "image.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, tzinfo=UTC))
    provider = FakeImageGateway()
    guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="image",
            timezone="Asia/Shanghai",
            daily_request_limit=1,
            daily_token_limit=None,
            failure_threshold=2,
            cooldown_seconds=60,
            lease_seconds=300,
        ),
        clock=clock,
    )
    gateway = ProtectedImageModelGateway(provider, guard, ALLOW_READINESS)

    result = await gateway.generate(ImageGenerationRequest(prompt="YCH", request_id="image-one"))
    with pytest.raises(ModelBudgetExceededError):
        await gateway.generate(ImageGenerationRequest(prompt="YCH", request_id="image-two"))

    assert result.artifacts
    assert provider.calls == ["image-one"]
    snapshot = await guard.snapshot()
    assert snapshot.token_limit is None
    assert snapshot.tokens_used == 0


@pytest.mark.asyncio
async def test_budget_day_uses_configured_timezone(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "timezone.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, 15, 59, tzinfo=UTC))
    provider = FakeChatGateway()
    guard = ModelCallGuard(repository, chat_policy(request_limit=1), clock=clock)
    gateway = ProtectedChatModelGateway(provider, guard, ALLOW_READINESS)

    await gateway.generate(chat_request("before-midnight"))
    assert (await guard.snapshot()).day_key == "2026-08-13"

    clock.advance(minutes=2)
    next_day = await guard.snapshot()
    assert next_day.day_key == "2026-08-14"
    assert next_day.requests_used == 0
    await gateway.generate(chat_request("after-midnight"))
    assert provider.calls == ["before-midnight", "after-midnight"]


def test_inlined_data_uri_does_not_inflate_token_reservation() -> None:
    from ych_bot.infrastructure.models.protection import _estimate_chat_reservation

    huge = "data:image/png;base64," + ("A" * 600_000)
    request = ChatGenerationRequest(
        request_id="vision-inline",
        messages=(ModelMessage(role=ModelRole.USER, content="看图", image_urls=(huge,)),),
        max_output_tokens=80,
    )
    assert _estimate_chat_reservation(request) < 5_000
