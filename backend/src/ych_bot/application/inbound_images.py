"""Inline inbound images to data URIs for the vision route."""

from __future__ import annotations

import base64
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from ych_bot.domain.errors import NapCatRequestError
from ych_bot.domain.models import safe_model_image_url, safe_napcat_image_file_id

MAX_INLINE_BYTES = 2_000_000
_INLINE_TIMEOUT_SECONDS = 10.0
_NAPCAT_IMAGE_PREFIX = "napcat-image:"
_IMAGE_PREFIXES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class ImageInliner(Protocol):
    def __call__(
        self, images: tuple[Any, ...]
    ) -> Awaitable[tuple[str, ...]]:  # pragma: no cover - protocol
        ...


class ImageFileFetcher(Protocol):
    async def get_image(self, *, file: str) -> dict[str, str]: ...


class ImageInlineError(ValueError):
    """An inbound image could not be turned into a local data URI."""


@dataclass(frozen=True, slots=True)
class InboundImageRef:
    url: str = ""
    file_id: str = ""

    def as_model_url(self) -> str:
        if self.url:
            return self.url
        if self.file_id:
            return f"{_NAPCAT_IMAGE_PREFIX}{self.file_id}"
        return ""


def _media_type(payload: bytes) -> str | None:
    for magic, media in _IMAGE_PREFIXES:
        if payload.startswith(magic):
            if magic == b"RIFF" and (len(payload) < 12 or payload[8:12] != b"WEBP"):
                return None
            return media
    return None


def _encode(payload: bytes) -> str:
    media = _media_type(payload)
    if media is None or not payload:
        raise ImageInlineError("image_inline_failed")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{media};base64,{encoded}"


def inbound_image_refs_from_data(data: dict[str, Any]) -> InboundImageRef | None:
    url = safe_model_image_url(data.get("url")) or safe_model_image_url(data.get("file"))
    file_id = safe_napcat_image_file_id(data.get("file"))
    if not url and not file_id:
        return None
    return InboundImageRef(url=url or "", file_id=file_id or "")


def inbound_image_refs_from_segments(segments: Any) -> tuple[InboundImageRef, ...]:
    refs: list[InboundImageRef] = []
    for item in segments or ():
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        ref = inbound_image_refs_from_data(data)
        if ref is not None:
            refs.append(ref)
    return tuple(refs)


def _normalize(images: tuple[Any, ...]) -> tuple[InboundImageRef, ...]:
    refs: list[InboundImageRef] = []
    for item in images:
        if isinstance(item, InboundImageRef):
            if item.url or item.file_id:
                refs.append(item)
            continue
        if not isinstance(item, str):
            continue
        if item.startswith("data:image/"):
            refs.append(InboundImageRef(url=item))
            continue
        if item.startswith(_NAPCAT_IMAGE_PREFIX):
            file_id = safe_napcat_image_file_id(item.removeprefix(_NAPCAT_IMAGE_PREFIX))
            if file_id:
                refs.append(InboundImageRef(file_id=file_id))
            continue
        url = safe_model_image_url(item)
        if url:
            refs.append(InboundImageRef(url=url))
    return tuple(refs)


async def _download_http(http: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        async with http.stream("GET", url) as response:
            if response.status_code != 200:
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_INLINE_BYTES:
                    raise ImageInlineError("image_inline_failed")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError:
        return None


def _read_local_image(path: str) -> bytes:
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ImageInlineError("image_inline_failed")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise ImageInlineError("image_inline_failed") from exc
    if size <= 0 or size > MAX_INLINE_BYTES:
        raise ImageInlineError("image_inline_failed")
    try:
        payload = candidate.read_bytes()
    except OSError as exc:
        raise ImageInlineError("image_inline_failed") from exc
    if len(payload) > MAX_INLINE_BYTES:
        raise ImageInlineError("image_inline_failed")
    return payload


async def _download_napcat(
    napcat: ImageFileFetcher, file_id: str, http: httpx.AsyncClient
) -> bytes:
    try:
        data = await napcat.get_image(file=file_id)
    except (NapCatRequestError, TypeError, ValueError) as exc:
        raise ImageInlineError("image_inline_failed") from exc
    raw_base64 = data.get("base64")
    if raw_base64:
        try:
            payload = base64.b64decode(raw_base64, validate=False)
        except (ValueError, TypeError) as exc:
            raise ImageInlineError("image_inline_failed") from exc
        return payload
    local = data.get("file") or ""
    if local and not local.startswith(("https://", "http://", "data:")):
        return _read_local_image(local)
    url = safe_model_image_url(data.get("url"))
    if url:
        payload = await _download_http(http, url)
        if payload is not None:
            return payload
    raise ImageInlineError("image_inline_failed")


async def inline_http_images(
    images: tuple[Any, ...],
    *,
    client: httpx.AsyncClient | None = None,
    napcat: ImageFileFetcher | None = None,
) -> tuple[str, ...]:
    refs = _normalize(images)
    if not refs:
        return ()
    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=_INLINE_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    inlined: list[str] = []
    try:
        for ref in refs:
            if ref.url.startswith("data:image/"):
                inlined.append(ref.url)
                continue
            payload: bytes | None = None
            http_ok = False
            if ref.url.startswith(("https://", "http://")):
                payload = await _download_http(http, ref.url)
                http_ok = payload is not None
            if http_ok:
                inlined.append(_encode(payload or b""))
                continue
            if napcat is not None and ref.file_id:
                inlined.append(_encode(await _download_napcat(napcat, ref.file_id, http)))
                continue
            raise ImageInlineError("image_inline_failed")
    finally:
        if owns_client:
            await http.aclose()
    return tuple(inlined)
