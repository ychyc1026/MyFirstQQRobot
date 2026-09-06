import json

import httpx
import pytest
from ych_bot.infrastructure.napcat.client import NapCatClient


@pytest.mark.asyncio
async def test_qzone_publish_and_delete_use_napcat_actions() -> None:
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        data = {"tid": "qzone-tid-1"} if request.url.path == "/send_qzone_msg" else None
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})

    client = NapCatClient(
        "http://napcat.test",
        "local-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        tid = await client.publish_qzone(
            content="YCH 测试草稿",
            images=["file://preview.png"],
            visibility=4,
        )
        await client.delete_qzone(tid)
    finally:
        await client.close()

    assert tid == "qzone-tid-1"
    assert requests == [
        (
            "/send_qzone_msg",
            {
                "content": "YCH 测试草稿",
                "images": ["file://preview.png"],
                "ugc_right": 4,
            },
        ),
        ("/delete_qzone_msg", {"tid": "qzone-tid-1"}),
    ]
