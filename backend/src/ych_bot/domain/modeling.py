"""Provider-independent contracts for chat and image models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: str
    image_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatGenerationRequest:
    messages: tuple[ModelMessage, ...]
    request_id: str
    temperature: float = 0.7
    max_output_tokens: int = 1000


@dataclass(frozen=True, slots=True)
class ChatGenerationResult:
    text: str
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    prompt: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ImageGenerationResult:
    artifacts: tuple[str, ...]
    provider_request_id: str | None = None


class ChatModelGateway(Protocol):
    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult: ...

    async def close(self) -> None: ...


class ImageModelGateway(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...

    async def close(self) -> None: ...


class ModelUnavailableError(RuntimeError):
    pass


class ModelBudgetExceededError(ModelUnavailableError):
    """A local request or token budget rejected the model call."""


class ModelCircuitOpenError(ModelUnavailableError):
    """A local circuit breaker rejected the model call."""


class ModelCallConflictError(ModelUnavailableError):
    """A duplicate local model request identifier was rejected."""


class ModelRequestError(RuntimeError):
    """A provider request failed without exposing credentials or response bodies."""


class ModelResponseError(RuntimeError):
    """A provider returned a success response with an invalid result shape."""


class DisabledChatModelGateway:
    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        del request
        raise ModelUnavailableError("chat model gateway is not configured")

    async def close(self) -> None:
        return None


class DisabledImageModelGateway:
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        del request
        raise ModelUnavailableError("image model gateway is not configured")

    async def close(self) -> None:
        return None
