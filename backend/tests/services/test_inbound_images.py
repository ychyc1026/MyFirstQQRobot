import base64

import httpx
import pytest
from ych_bot.application.inbound_images import (
    ImageInlineError,
    InboundImageRef,
    inline_http_images,
)
from ych_bot.qualification.fixtures.vision import deterministic_png

SAFE_URL = "https://example.invalid/probe.png"
FILE_ID = "nt-image-file-001"


@pytest.mark.asyncio
async def test_inline_http_images_uses_fake_transport_only() -> None:
    payload = deterministic_png(32, 32)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == SAFE_URL
        return httpx.Response(200, content=payload, headers={"content-type": "image/png"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        inlined = await inline_http_images((SAFE_URL,), client=client)

    assert len(inlined) == 1
    assert inlined[0].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_inline_http_images_rejects_non_image_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-an-image", headers={"content-type": "image/png"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(ImageInlineError):
            await inline_http_images((SAFE_URL,), client=client)


@pytest.mark.asyncio
async def test_inline_falls_back_to_napcat_get_image_when_cdn_rejects() -> None:
    payload = deterministic_png(16, 16)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append("http")
        return httpx.Response(400, content=b'{"msg":"denied"}')

    class FakeNapCat:
        async def get_image(self, *, file: str) -> dict[str, str]:
            assert file == FILE_ID
            calls.append("napcat")
            return {"base64": base64.b64encode(payload).decode("ascii")}

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        inlined = await inline_http_images(
            (InboundImageRef(url=SAFE_URL, file_id=FILE_ID),),
            client=client,
            napcat=FakeNapCat(),
        )

    assert calls == ["http", "napcat"]
    assert inlined[0].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_inline_does_not_call_napcat_when_direct_download_works() -> None:
    payload = deterministic_png(16, 16)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    class FakeNapCat:
        async def get_image(self, *, file: str) -> dict[str, str]:
            raise AssertionError("napcat should not run")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        inlined = await inline_http_images(
            (InboundImageRef(url=SAFE_URL, file_id=FILE_ID),),
            client=client,
            napcat=FakeNapCat(),
        )

    assert inlined[0].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_inline_fails_closed_when_cdn_rejects_and_napcat_is_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"no")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(ImageInlineError):
            await inline_http_images(
                (InboundImageRef(url=SAFE_URL, file_id=FILE_ID),),
                client=client,
            )
