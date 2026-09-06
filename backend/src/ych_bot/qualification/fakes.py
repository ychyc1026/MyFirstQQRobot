"""Fake providers for no-network qualification."""

from __future__ import annotations

from enum import StrEnum

from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ImageGenerationRequest,
    ImageGenerationResult,
    ModelBudgetExceededError,
    ModelCircuitOpenError,
    ModelRequestError,
    ModelResponseError,
)


class FakeProviderBehavior(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RETRYABLE = "retryable"
    INVALID = "invalid"
    QUOTA = "quota"
    CIRCUIT = "circuit"
    AMBIGUOUS = "ambiguous"


class FakeAmbiguousInterruption(RuntimeError):
    """A fake provider was interrupted after a request may have been billed."""


def _normalize_behavior(behavior: FakeProviderBehavior | str) -> FakeProviderBehavior:
    return FakeProviderBehavior(behavior)


def _raise_classified_failure(behavior: FakeProviderBehavior, capability: str) -> None:
    if behavior is FakeProviderBehavior.TIMEOUT:
        raise TimeoutError(f"fake {capability} timeout")
    if behavior is FakeProviderBehavior.RETRYABLE:
        raise ModelRequestError(f"fake {capability} retryable failure")
    if behavior is FakeProviderBehavior.INVALID:
        raise ModelResponseError(f"fake {capability} invalid response")
    if behavior is FakeProviderBehavior.QUOTA:
        raise ModelBudgetExceededError(f"fake {capability} quota exhausted")
    if behavior is FakeProviderBehavior.CIRCUIT:
        raise ModelCircuitOpenError(f"fake {capability} circuit open")
    if behavior is FakeProviderBehavior.AMBIGUOUS:
        raise FakeAmbiguousInterruption(f"fake {capability} ambiguous interruption")


class FailIfConstructed:
    def __init__(self, name: str) -> None:
        self.name = name

    async def generate(self, request: object) -> object:
        del request
        raise AssertionError(f"{self.name} gateway must not be called")

    async def close(self) -> None:
        raise AssertionError(f"{self.name} gateway must not be closed as a real client")


class _FakeTextGateway:
    capability = "text"
    success_text = "synthetic qualification output"

    def __init__(self, behavior: FakeProviderBehavior | str = FakeProviderBehavior.SUCCESS) -> None:
        self.behavior = _normalize_behavior(behavior)
        self.calls = 0
        self.last_request: ChatGenerationRequest | None = None

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.calls += 1
        self.last_request = request
        if self.behavior is not FakeProviderBehavior.SUCCESS:
            _raise_classified_failure(self.behavior, self.capability)
        return ChatGenerationResult(
            text=self.success_text,
            provider_request_id=f"fake-{self.capability}-1",
            input_tokens=8,
            output_tokens=12,
        )

    async def close(self) -> None:
        return None


class FakeChatGateway(_FakeTextGateway):
    capability = "chat"
    success_text = "我是 YCH，由维护者开发。"


class FakeVisionGateway(_FakeTextGateway):
    capability = "vision"
    success_text = "图中是合成蓝色方块，不能确定未给出的内容。"


class FakeStatsGateway(_FakeTextGateway):
    capability = "stats"
    success_text = '{"count": 2, "sum": 5, "uncertain": false}'


class FakeImageGateway:
    def __init__(
        self,
        behavior: FakeProviderBehavior | str = FakeProviderBehavior.SUCCESS,
        artifacts: tuple[str, ...] | None = None,
    ) -> None:
        self.behavior = _normalize_behavior(behavior)
        self.calls = 0
        self.artifacts = artifacts or ("qualification/image-blue-square.png",)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls += 1
        if self.behavior is not FakeProviderBehavior.SUCCESS:
            _raise_classified_failure(self.behavior, "image")
        del request
        return ImageGenerationResult(
            artifacts=self.artifacts,
            provider_request_id="fake-image-1",
        )

    async def close(self) -> None:
        return None
