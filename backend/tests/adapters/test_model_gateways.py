from __future__ import annotations

import json

import httpx
import pytest
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ImageGenerationRequest,
    ModelMessage,
    ModelRequestError,
    ModelResponseError,
    ModelRole,
)
from ych_bot.infrastructure.models import (
    OpenAICompatibleChatGateway,
    OpenAICompatibleImageGateway,
)


@pytest.mark.asyncio
async def test_chat_gateway_uses_its_own_route_key_model_and_usage() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
                "client_request_id": request.headers.get("x-client-request-id"),
                "payload": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-chat-id"},
            json={
                "id": "body-chat-id",
                "choices": [{"message": {"content": "chat result"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    gateway = OpenAICompatibleChatGateway(
        base_url="https://chat.example/v1",
        api_key="chat-key",
        model="chat-model",
        transport=httpx.MockTransport(handler),
        retry_base_delay_seconds=0,
    )
    try:
        result = await gateway.generate(
            ChatGenerationRequest(
                request_id="local-chat-id",
                messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
            )
        )
    finally:
        await gateway.close()

    assert result.text == "chat result"
    assert result.provider_request_id == "provider-chat-id"
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    assert captured == [
        {
            "path": "/v1/chat/completions",
            "authorization": "Bearer chat-key",
            "client_request_id": "local-chat-id",
            "payload": {
                "model": "chat-model",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.7,
                "max_tokens": 1000,
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_gateway_sends_multimodal_image_parts() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "a cat"}}]},
        )

    gateway = OpenAICompatibleChatGateway(
        base_url="https://vision.example/v1",
        api_key="vision-key",
        model="Qwen/Qwen3-VL-8B-Instruct",
        transport=httpx.MockTransport(handler),
        retry_base_delay_seconds=0,
    )
    try:
        result = await gateway.generate(
            ChatGenerationRequest(
                request_id="vision-id",
                messages=(
                    ModelMessage(
                        role=ModelRole.USER,
                        content="这是什么",
                        image_urls=("https://example.com/a.png",),
                    ),
                ),
            )
        )
    finally:
        await gateway.close()

    assert result.text == "a cat"
    assert captured[0]["model"] == "Qwen/Qwen3-VL-8B-Instruct"
    assert captured[0]["messages"][0]["content"] == [
        {"type": "text", "text": "这是什么"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
    ]


@pytest.mark.asyncio
async def test_image_gateway_is_independent_from_chat_configuration() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
                "payload": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "provider-image-id",
                "data": [
                    {"url": "https://images.example/result.png"},
                    {"b64_json": "YWJj"},
                ],
            },
        )

    gateway = OpenAICompatibleImageGateway(
        base_url="https://image.example/v1",
        api_key="image-key",
        model="image-model",
        transport=httpx.MockTransport(handler),
        retry_base_delay_seconds=0,
    )
    try:
        result = await gateway.generate(
            ImageGenerationRequest(prompt="a clean YCH dashboard", request_id="local-image-id")
        )
    finally:
        await gateway.close()

    assert result.provider_request_id == "provider-image-id"
    assert result.artifacts == (
        "https://images.example/result.png",
        "data:image/png;base64,YWJj",
    )
    assert captured == [
        {
            "path": "/v1/images/generations",
            "authorization": "Bearer image-key",
            "payload": {
                "model": "image-model",
                "prompt": "a clean YCH dashboard",
                "response_format": "b64_json",
            },
        }
    ]


@pytest.mark.asyncio
async def test_retryable_status_retries_without_leaking_response_body() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, json={"error": "sensitive-provider-body"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "recovered"}}]},
        )

    gateway = OpenAICompatibleChatGateway(
        base_url="https://chat.example/v1",
        api_key="secret-chat-key",
        model="chat-model",
        max_retries=2,
        transport=httpx.MockTransport(handler),
        retry_base_delay_seconds=0,
    )
    try:
        result = await gateway.generate(
            ChatGenerationRequest(
                request_id="retry-id",
                messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
            )
        )
    finally:
        await gateway.close()

    assert attempts == 3
    assert result.text == "recovered"


@pytest.mark.asyncio
async def test_provider_error_and_invalid_shape_are_safely_reported() -> None:
    def rejected(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "body-must-not-leak"})

    rejected_gateway = OpenAICompatibleChatGateway(
        base_url="https://chat.example/v1",
        api_key="key-must-not-leak",
        model="chat-model",
        max_retries=0,
        transport=httpx.MockTransport(rejected),
    )
    request = ChatGenerationRequest(
        request_id="failure-id",
        messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
    )
    try:
        with pytest.raises(ModelRequestError) as error:
            await rejected_gateway.generate(request)
    finally:
        await rejected_gateway.close()
    assert "status=401" in str(error.value)
    assert "body-must-not-leak" not in str(error.value)
    assert "key-must-not-leak" not in str(error.value)

    def malformed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    malformed_gateway = OpenAICompatibleChatGateway(
        base_url="https://chat.example/v1",
        api_key="",
        model="chat-model",
        transport=httpx.MockTransport(malformed),
    )
    try:
        with pytest.raises(ModelResponseError, match="no choices"):
            await malformed_gateway.generate(request)
    finally:
        await malformed_gateway.close()


@pytest.mark.asyncio
async def test_transport_failure_retries_a_bounded_number_of_times() -> None:
    attempts = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    gateway = OpenAICompatibleChatGateway(
        base_url="https://chat.example/v1",
        api_key="",
        model="chat-model",
        max_retries=1,
        transport=httpx.MockTransport(timeout),
        retry_base_delay_seconds=0,
    )
    try:
        with pytest.raises(ModelRequestError, match="2 attempt"):
            await gateway.generate(
                ChatGenerationRequest(
                    request_id="timeout-id",
                    messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
                )
            )
    finally:
        await gateway.close()
    assert attempts == 2
