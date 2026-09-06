"""Confined storage for qualification-generated images."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_FETCH_BYTES = 2_000_000
_ALLOWED_HOST_SUFFIXES = ("siliconflow.cn", "siliconflow.com")
_ALLOWED_HOST_LABELS = ("sf-maas",)


@dataclass(frozen=True, slots=True)
class QualificationStoredArtifact:
    artifact_id: str
    case_id: str
    relative_path: str
    content_sha256: str
    mime_type: str
    size_bytes: int
    pixel_width: int | None
    pixel_height: int | None


class QualificationImageStore:
    def __init__(self, generated_root: Path) -> None:
        self._root = generated_root.resolve()

    def write(
        self,
        *,
        run_id: str,
        case_id: str,
        payload: bytes,
        mime_type: str,
    ) -> QualificationStoredArtifact:
        if mime_type != "image/png":
            raise ValueError("qualification images must be png")
        if not _SAFE_ID.match(run_id) or not _SAFE_ID.match(case_id):
            raise ValueError("qualification image path is not confined")
        if not payload.startswith(_PNG_MAGIC):
            raise ValueError("qualification images must be png")
        artifact_id = uuid4().hex
        relative = Path("qualification") / run_id / f"{artifact_id}.png"
        destination = (self._root / relative).resolve()
        if not str(destination).startswith(str(self._root)):
            raise ValueError("qualification image path is not confined")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        width, height = _png_size(payload)
        return QualificationStoredArtifact(
            artifact_id=artifact_id,
            case_id=case_id,
            relative_path=relative.as_posix(),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            mime_type="image/png",
            size_bytes=len(payload),
            pixel_width=width,
            pixel_height=height,
        )

    def ingest(
        self,
        *,
        run_id: str,
        case_id: str,
        artifacts: tuple[str, ...],
    ) -> QualificationStoredArtifact:
        if len(artifacts) != 1:
            raise ValueError("qualification images must be png")
        payload = _decode_png_artifact(artifacts[0])
        return self.write(
            run_id=run_id,
            case_id=case_id,
            payload=payload,
            mime_type="image/png",
        )

    def preview_metadata(self, artifact: QualificationStoredArtifact) -> dict[str, object]:
        return {
            "artifact_id": artifact.artifact_id,
            "mime_type": artifact.mime_type,
            "size_bytes": artifact.size_bytes,
            "pixel_width": artifact.pixel_width,
            "pixel_height": artifact.pixel_height,
            "relative_path": artifact.relative_path,
            "expires_at": datetime.now(UTC).isoformat(),
        }


def _allowed_provider_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _ALLOWED_HOST_SUFFIXES
    ):
        return True
    labels = normalized.split(".")
    return any(label in _ALLOWED_HOST_LABELS for label in labels)


def _fetch_allowlisted_png(url: str) -> bytes:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or not _allowed_provider_host(host):
        raise ValueError("qualification image path is not confined")
    if parsed.username or parsed.password:
        raise ValueError("qualification image path is not confined")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise ValueError("qualification images must be png")
    payload = response.content
    if len(payload) > _MAX_FETCH_BYTES or not payload.startswith(_PNG_MAGIC):
        raise ValueError("qualification images must be png")
    return payload


def _decode_png_artifact(artifact: str) -> bytes:
    if artifact.startswith("data:image/png;base64,"):
        try:
            return base64.b64decode(artifact.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("qualification images must be png") from exc
    if artifact.startswith("https://"):
        return _fetch_allowlisted_png(artifact)
    raise ValueError("qualification image path is not confined")


def _png_size(payload: bytes) -> tuple[int | None, int | None]:
    if len(payload) < 24:
        return None, None
    try:
        width, height = struct.unpack(">II", payload[16:24])
    except struct.error:
        return None, None
    return int(width), int(height)
