"""Minimal OneBot HTTP client for the durable outbox worker."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from ych_bot.domain.errors import NapCatDeliveryUnknownError, NapCatRequestError
from ych_bot.domain.models import ConversationKind, OutboundMessage, safe_napcat_image_file_id


class NapCatClient:
    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=30,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, action: str, payload: dict) -> dict:
        response = await self._client.post(f"/{action}", json=payload)
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "ok" or int(result.get("retcode", -1)) != 0:
            raise NapCatRequestError(f"NapCat rejected {action}: retcode={result.get('retcode')}")
        return result

    async def send_message(self, message: OutboundMessage) -> dict:
        if message.conversation_kind is ConversationKind.PRIVATE:
            action = "/send_private_msg"
            target = {"user_id": message.target_id}
        else:
            action = "/send_group_msg"
            target = {"group_id": message.target_id}

        try:
            return await self._call(
                action.removeprefix("/"),
                {
                    **target,
                    "message": [segment.as_onebot() for segment in message.segments],
                },
            )
        except NapCatRequestError:
            raise
        except httpx.HTTPStatusError as exc:
            raise NapCatRequestError("NapCat rejected the send request over HTTP") from exc
        except (httpx.TransportError, ValueError) as exc:
            raise NapCatDeliveryUnknownError("NapCat send outcome could not be confirmed") from exc

    async def get_friend_ids(self) -> list[str]:
        result = await self._call("get_friend_list", {"no_cache": True})
        friends = result.get("data") or []
        return sorted(
            {
                str(friend.get("user_id", "")).strip()
                for friend in friends
                if isinstance(friend, dict) and str(friend.get("user_id", "")).strip()
            }
        )

    async def get_friend_history(self, *, user_qq: str, count: int) -> list[dict]:
        result = await self._call(
            "get_friend_msg_history",
            {"user_id": user_qq, "count": count},
        )
        data = result.get("data") or {}
        if isinstance(data, list):
            return data
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return messages if isinstance(messages, list) else []

    async def publish_qzone(
        self,
        *,
        content: str,
        images: list[str] | None = None,
        visibility: int = 1,
        target_uins: list[str] | None = None,
    ) -> str:
        payload: dict = {"content": content, "ugc_right": visibility}
        if images:
            payload["images"] = images
        if target_uins:
            payload["target_uins"] = target_uins
        result = await self._call("send_qzone_msg", payload)
        tid = str((result.get("data") or {}).get("tid", "")).strip()
        if not tid:
            raise NapCatRequestError("NapCat send_qzone_msg returned no tid")
        return tid

    async def delete_qzone(self, tid: str) -> None:
        await self._call("delete_qzone_msg", {"tid": tid})

    async def get_image(self, *, file: str) -> dict[str, str]:
        file_id = safe_napcat_image_file_id(file)
        if file_id is None:
            raise NapCatRequestError("NapCat rejected get_image: invalid file id")
        result = await self._call("get_image", {"file": file_id})
        data = result.get("data") or {}
        if not isinstance(data, dict):
            raise NapCatRequestError("NapCat get_image returned no image data")
        allowlisted: dict[str, str] = {}
        for key in ("base64", "file", "url"):
            value = data.get(key)
            if isinstance(value, str) and value:
                allowlisted[key] = value
        if not allowlisted:
            raise NapCatRequestError("NapCat get_image returned no image data")
        return allowlisted

    async def get_qzone_msg_list(self, *, user_qq: str, count: int) -> dict:
        result = await self._call(
            "get_qzone_msg_list",
            {"user_id": user_qq, "count": count},
        )
        data = result.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        items_raw = data.get("items") or data.get("msg_list") or data.get("feeds") or []
        if not isinstance(items_raw, list):
            items_raw = []
        items = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("tid") or item.get("id") or "").strip()
            content = str(item.get("content") or item.get("text") or "").strip()
            published_at = item.get("published_at") or item.get("create_time")
            parsed = {
                "tid": tid or None,
                "content": content,
                "published_at": str(published_at) if published_at else None,
                "has_video": bool(
                    item.get("has_video") or item.get("video") or item.get("video_url")
                ),
                "cover_url": item.get("cover") or item.get("cover_url") or item.get("thumb"),
                "images": item.get("images") or item.get("pics") or item.get("pic"),
            }
            if tid or content or parsed["has_video"] or parsed["images"] or parsed["cover_url"]:
                items.append(parsed)
        nickname = data.get("nickname") or data.get("nick")
        signature = data.get("signature") or data.get("sig")
        return {
            "nickname": str(nickname).strip() if nickname else None,
            "signature": str(signature).strip() if signature else None,
            "items": items,
        }


class LazyNapCatClient:
    """Delay construction of the network adapter until a protected effect uses it.

    Application composition, local readiness checks and dashboard-only operations must
    stay free of real HTTP client construction. The surrounding services still receive
    one stable object, while their existing gates and the readiness guard decide whether
    any method may reach the underlying adapter.
    """

    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        *,
        factory: Callable[[str, str], NapCatClient] | None = None,
    ) -> None:
        self._base_url = base_url
        self._access_token = access_token
        self._factory = factory or NapCatClient
        self._client: NapCatClient | None = None

    @property
    def constructed(self) -> bool:
        return self._client is not None

    def _get(self) -> NapCatClient:
        if self._client is None:
            self._client = self._factory(self._base_url, self._access_token)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def send_message(self, message: OutboundMessage) -> dict:
        return await self._get().send_message(message)

    async def get_friend_ids(self) -> list[str]:
        return await self._get().get_friend_ids()

    async def get_friend_history(self, *, user_qq: str, count: int) -> list[dict]:
        return await self._get().get_friend_history(user_qq=user_qq, count=count)

    async def publish_qzone(
        self,
        *,
        content: str,
        images: list[str] | None = None,
        visibility: int = 1,
        target_uins: list[str] | None = None,
    ) -> str:
        return await self._get().publish_qzone(
            content=content,
            images=images,
            visibility=visibility,
            target_uins=target_uins,
        )

    async def delete_qzone(self, tid: str) -> None:
        await self._get().delete_qzone(tid)

    async def get_image(self, *, file: str) -> dict[str, str]:
        return await self._get().get_image(file=file)

    async def get_qzone_msg_list(self, *, user_qq: str, count: int) -> dict:
        return await self._get().get_qzone_msg_list(user_qq=user_qq, count=count)
