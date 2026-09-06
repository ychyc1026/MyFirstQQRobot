"""Independent OpenAI-compatible HTTP adapters for chat and image generation."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ImageGenerationRequest,
    ImageGenerationResult,
    ModelMessage,
    ModelRequestError,
    ModelResponseError,
)

_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class _OpenAICompatibleGateway:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None,
        retry_base_delay_seconds: float,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("model API base must use http or https")
        if not model.strip():
            raise ValueError("model name must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("model timeout must be positive")
        if not (0 <= max_retries <= 5):
            raise ValueError("model max_retries must be between 0 and 5")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry delay must not be negative")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._model = model.strip()
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        request_id: str,
    ) -> tuple[dict[str, Any], str | None]:
        last_transport_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    path,
                    json=payload,
                    headers={"X-Client-Request-Id": request_id},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_transport_error = exc
                if attempt >= self._max_retries:
                    raise ModelRequestError(
                        f"model transport failed after {attempt + 1} attempt(s): "
                        f"{type(exc).__name__}"
                    ) from exc
                await self._retry_delay(attempt)
                continue

            provider_request_id = response.headers.get("x-request-id")
            if response.status_code >= 400:
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                    await self._retry_delay(attempt)
                    continue
                raise ModelRequestError(
                    "model HTTP request failed: "
                    f"status={response.status_code}, request_id={provider_request_id or 'unknown'}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ModelResponseError("model response was not valid JSON") from exc
            if not isinstance(data, dict):
                raise ModelResponseError("model response JSON must be an object")
            return data, provider_request_id

        raise ModelRequestError(f"model transport failed: {type(last_transport_error).__name__}")

    async def _retry_delay(self, attempt: int) -> None:
        delay = self._retry_base_delay_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)


class OpenAICompatibleChatGateway(_OpenAICompatibleGateway):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_base_delay_seconds: float = 0.25,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        self._extra_payload = dict(extra_payload or {})

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        data, header_request_id = await self._post(
            "chat/completions",
            payload={
                "model": self._model,
                "messages": [_chat_message_payload(message) for message in request.messages],
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                **self._extra_payload,
            },
            request_id=request.request_id,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseError("chat response contained no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelResponseError("chat response contained no text content")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ChatGenerationResult(
            text=content,
            provider_request_id=_provider_request_id(data, header_request_id),
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )


class OpenAICompatibleImageGateway(_OpenAICompatibleGateway):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        response_format: str = "b64_json",
        timeout_seconds: float = 180,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_base_delay_seconds: float = 0.5,
    ) -> None:
        if response_format not in {"b64_json", "url"}:
            raise ValueError("image response_format must be b64_json or url")
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        self._response_format = response_format

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        data, header_request_id = await self._post(
            "images/generations",
            payload={
                "model": self._model,
                "prompt": request.prompt,
                "response_format": self._response_format,
            },
            request_id=request.request_id,
        )
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ModelResponseError("image response contained no artifacts")
        artifacts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("url"), str):
                artifacts.append(item["url"])
            elif isinstance(item.get("b64_json"), str):
                artifacts.append(f"data:image/png;base64,{item['b64_json']}")
        if not artifacts:
            raise ModelResponseError("image response contained no supported artifact")
        return ImageGenerationResult(
            artifacts=tuple(artifacts),
            provider_request_id=_provider_request_id(data, header_request_id),
        )


def _chat_message_payload(message: ModelMessage) -> dict[str, Any]:
    if not message.image_urls:
        return {"role": message.role.value, "content": message.content}
    parts: list[dict[str, Any]] = []
    if message.content.strip():
        parts.append({"type": "text", "text": message.content})
    for url in message.image_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": message.role.value, "content": parts}


def _provider_request_id(data: dict[str, Any], header_value: str | None) -> str | None:
    response_id = data.get("id")
    return header_value or (response_id if isinstance(response_id, str) else None)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
