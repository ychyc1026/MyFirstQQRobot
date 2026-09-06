from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from ych_bot.domain.errors import NapCatDeliveryUnknownError, NapCatRequestError
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.infrastructure.napcat.client import NapCatClient


def outbound() -> OutboundMessage:
    return OutboundMessage(
        id=str(uuid4()),
        idempotency_key=str(uuid4()),
        conversation_kind=ConversationKind.PRIVATE,
        target_id="123456789",
        segments=(MessageSegment("text", {"text": "fake only"}),),
    )


@pytest.mark.asyncio
async def test_napcat_confirmed_retcode_is_a_rejection() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"status": "failed", "retcode": 1200, "data": None},
        )
    )
    client = NapCatClient("http://napcat.invalid", transport=transport)
    try:
        with pytest.raises(NapCatRequestError, match="rejected"):
            await client.send_message(outbound())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_napcat_transport_timeout_has_unknown_delivery() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fake timeout", request=request)

    client = NapCatClient(
        "http://napcat.invalid",
        transport=httpx.MockTransport(timeout),
    )
    try:
        with pytest.raises(NapCatDeliveryUnknownError, match="could not be confirmed"):
            await client.send_message(outbound())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_napcat_get_image_posts_file_id_only() -> None:
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={"status": "ok", "retcode": 0, "data": {"base64": "ZmFrZQ=="}},
        )

    client = NapCatClient("http://napcat.test", transport=httpx.MockTransport(handler))
    try:
        data = await client.get_image(file="nt-image-file-001")
    finally:
        await client.close()

    assert data == {"base64": "ZmFrZQ=="}
    assert requests == [("/get_image", {"file": "nt-image-file-001"})]
