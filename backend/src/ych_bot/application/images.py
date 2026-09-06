"""Local-first image generation tasks with artifact validation and approval staging."""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ych_bot.domain.images import (
    ImageIntendedUse,
    ImageReviewEngine,
    ImageReviewResult,
    ImageTaskStatus,
)
from ych_bot.domain.modeling import ImageGenerationRequest, ImageModelGateway
from ych_bot.infrastructure.database import SQLiteRepository


class ImageGenerationTaskError(RuntimeError):
    pass


class DisabledImageReviewEngine:
    def review(
        self,
        *,
        content: bytes,
        media_type: str,
        content_sha256: str,
    ) -> ImageReviewResult:
        del content, media_type, content_sha256
        raise ImageGenerationTaskError("image content review engine is not configured")


@dataclass(frozen=True, slots=True)
class StoredImageArtifact:
    id: str
    storage_path: str
    content_sha256: str
    media_type: str
    byte_count: int
    width: int
    height: int

    def as_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "storage_path": self.storage_path,
            "content_sha256": self.content_sha256,
            "media_type": self.media_type,
            "byte_count": self.byte_count,
            "width": self.width,
            "height": self.height,
        }


class ImageGenerationService:
    def __init__(
        self,
        repository: SQLiteRepository,
        gateway: ImageModelGateway,
        *,
        project_root: Path,
        owner_qq: str,
        model_route: str,
        enabled: bool,
        max_artifact_megabytes: int,
        max_pixels: int,
        max_artifacts: int,
        review_enabled: bool = False,
        review_engine: ImageReviewEngine | None = None,
        orphan_scan_enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._root = project_root.resolve()
        self._owner_qq = owner_qq
        self._model_route = model_route or "unconfigured"
        self._enabled = enabled
        self._max_bytes = max_artifact_megabytes * 1024 * 1024
        self._max_pixels = max_pixels
        self._max_artifacts = max_artifacts
        self._review_enabled = review_enabled
        self._review_engine = review_engine
        self._orphan_scan_enabled = orphan_scan_enabled
        self._storage = (self._root / "storage" / "generated" / "images").resolve()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def review_enabled(self) -> bool:
        return self._review_enabled

    @property
    def orphan_scan_enabled(self) -> bool:
        return self._orphan_scan_enabled

    async def create_task(
        self,
        *,
        prompt: str,
        intended_use: ImageIntendedUse,
        requested_by: str,
        request_source: str,
    ) -> dict[str, object]:
        normalized = prompt.strip()
        if not normalized:
            raise ImageGenerationTaskError("image prompt must not be empty")
        if len(normalized) > 4000:
            raise ImageGenerationTaskError("image prompt exceeds 4000 characters")
        if "\x00" in normalized:
            raise ImageGenerationTaskError("image prompt contains an invalid null character")
        if request_source not in {"dashboard", "owner_qq"}:
            raise ImageGenerationTaskError("unsupported image task source")
        status = ImageTaskStatus.DRAFT if self._enabled else ImageTaskStatus.AWAITING_MODEL_CONFIG
        return await self._repository.create_image_task(
            task_id=str(uuid4()),
            prompt=normalized,
            intended_use=intended_use.value,
            status=status,
            requested_by=requested_by,
            request_source=request_source,
            model_route=self._model_route,
        )

    async def generate(self, task_id: str) -> dict[str, object]:
        if not self._enabled:
            raise ImageGenerationTaskError("image model route is disabled")
        task = await self._repository.image_task(task_id)
        if task is None:
            raise ImageGenerationTaskError("image task not found")
        request_id = str(uuid4())
        if not await self._repository.claim_image_task(task_id=task_id, request_id=request_id):
            raise ImageGenerationTaskError("image task is not available for generation")

        written: list[Path] = []
        try:
            result = await self._gateway.generate(
                ImageGenerationRequest(prompt=task["prompt"], request_id=request_id)
            )
            records, written = self._store_artifacts(task_id, result.artifacts)
            reviews, blocked = self._review_artifacts(records)
            approval_id = str(uuid4())
            approval_code = secrets.token_hex(3).upper()
            completed = await self._repository.complete_image_task(
                task_id=task_id,
                request_id=request_id,
                provider_request_id=result.provider_request_id,
                artifacts=tuple(record.as_record() for record in records),
                approval_id=approval_id,
                approval_code=approval_code,
                requested_to=self._owner_qq,
                report_id=str(uuid4()),
                reviews=tuple(reviews),
                blocked=blocked,
            )
            if not completed:
                raise ImageGenerationTaskError("image task changed while completing generation")
        except Exception as exc:
            self._cleanup_written(written)
            await self._repository.fail_image_task(
                task_id=task_id,
                request_id=request_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            if isinstance(exc, ImageGenerationTaskError):
                raise
            raise ImageGenerationTaskError(
                f"image generation failed: {type(exc).__name__}"
            ) from exc

        completed_task = await self._repository.image_task(task_id)
        if completed_task is None:
            raise ImageGenerationTaskError("completed image task could not be read")
        return completed_task

    async def task(self, task_id: str) -> dict[str, object]:
        task = await self._repository.image_task(task_id)
        if task is None:
            raise ImageGenerationTaskError("image task not found")
        return task

    async def tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if status is not None:
            try:
                ImageTaskStatus(status)
            except ValueError as exc:
                raise ImageGenerationTaskError("invalid image task status") from exc
        return await self._repository.image_tasks(status=status, limit=limit)

    async def renew_approval(self, task_id: str) -> dict[str, object]:
        task = await self.task(task_id)
        status = str(task["status"])
        if status == ImageTaskStatus.PENDING_APPROVAL.value:
            expires_at = task.get("approval_expires_at")
            if isinstance(expires_at, str) and datetime.fromisoformat(expires_at) >= datetime.now(
                UTC
            ):
                raise ImageGenerationTaskError("image approval is not expired")
        elif status != ImageTaskStatus.APPROVAL_EXPIRED.value:
            raise ImageGenerationTaskError("image approval is not expired")
        renewed = await self._repository.renew_image_approval(
            task_id=task_id,
            approval_id=str(uuid4()),
            approval_code=secrets.token_hex(3).upper(),
            requested_to=self._owner_qq,
            report_id=str(uuid4()),
        )
        if not renewed:
            raise ImageGenerationTaskError("image approval could not be renewed")
        return await self.task(task_id)

    async def scan_orphans(self, *, created_by: str) -> dict[str, object]:
        if not self._orphan_scan_enabled:
            raise ImageGenerationTaskError("orphan scan is disabled")
        tracked = set(await self._repository.image_artifact_paths())
        orphans: list[str] = []
        if self._storage.is_dir():
            for path in sorted(self._storage.rglob("*")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                relative = path.resolve().relative_to(self._root).as_posix()
                if relative not in tracked:
                    orphans.append(relative)
        missing = [
            storage_path
            for storage_path in sorted(tracked)
            if not (self._root / storage_path).is_file()
        ]
        report_id = str(uuid4()) if orphans or missing else None
        return await self._repository.record_image_orphan_scan(
            scan_id=str(uuid4()),
            orphan_count=len(orphans),
            missing_count=len(missing),
            details={"orphans": orphans, "missing": missing},
            created_by=created_by,
            report_id=report_id,
        )

    async def artifact_file(self, artifact_id: str) -> tuple[Path, str]:
        artifact = await self._repository.image_artifact(artifact_id)
        if artifact is None:
            raise ImageGenerationTaskError("image artifact not found")
        path = (self._root / artifact["storage_path"]).resolve()
        if self._storage not in path.parents:
            raise ImageGenerationTaskError("image artifact path escaped storage root")
        if not path.is_file():
            raise ImageGenerationTaskError("image artifact file is missing")
        content = path.read_bytes()
        if len(content) != artifact["byte_count"]:
            raise ImageGenerationTaskError("image artifact size verification failed")
        if hashlib.sha256(content).hexdigest() != artifact["content_sha256"]:
            raise ImageGenerationTaskError("image artifact integrity verification failed")
        return path, artifact["media_type"]

    def _store_artifacts(
        self,
        task_id: str,
        artifacts: tuple[str, ...],
    ) -> tuple[tuple[StoredImageArtifact, ...], list[Path]]:
        if not artifacts:
            raise ImageGenerationTaskError("image model returned no artifacts")
        if len(artifacts) > self._max_artifacts:
            raise ImageGenerationTaskError("image model returned too many artifacts")
        destination_dir = (self._storage / task_id).resolve()
        if self._storage not in destination_dir.parents:
            raise ImageGenerationTaskError("invalid image artifact destination")
        destination_dir.mkdir(parents=True, exist_ok=True)
        stored: list[StoredImageArtifact] = []
        written: list[Path] = []
        try:
            for raw in artifacts:
                content, media_type, suffix, width, height = self._decode_artifact(raw)
                artifact_id = str(uuid4())
                destination = destination_dir / f"{artifact_id}{suffix}"
                temporary = destination_dir / f".{artifact_id}.tmp"
                temporary.write_bytes(content)
                temporary.replace(destination)
                written.append(destination)
                stored.append(
                    StoredImageArtifact(
                        id=artifact_id,
                        storage_path=destination.relative_to(self._root).as_posix(),
                        content_sha256=hashlib.sha256(content).hexdigest(),
                        media_type=media_type,
                        byte_count=len(content),
                        width=width,
                        height=height,
                    )
                )
        except Exception:
            self._cleanup_written(written)
            if destination_dir.is_dir() and not any(destination_dir.iterdir()):
                destination_dir.rmdir()
            raise
        return tuple(stored), written

    def _review_artifacts(
        self,
        records: tuple[StoredImageArtifact, ...],
    ) -> tuple[list[dict[str, object]], bool]:
        if not self._review_enabled:
            return [], False
        engine = self._review_engine or DisabledImageReviewEngine()
        reviews: list[dict[str, object]] = []
        blocked = False
        for record in records:
            result = engine.review(
                content=(self._root / record.storage_path).read_bytes(),
                media_type=record.media_type,
                content_sha256=record.content_sha256,
            )
            if result.verdict not in {"allow", "block"}:
                raise ImageGenerationTaskError("image review engine returned an invalid verdict")
            reviews.append(
                {
                    "artifact_id": record.id,
                    "verdict": result.verdict,
                    "labels": list(result.labels),
                    "engine": result.engine,
                }
            )
            if result.verdict == "block":
                blocked = True
        return reviews, blocked

    def _decode_artifact(self, raw: str) -> tuple[bytes, str, str, int, int]:
        if raw.startswith(("http://", "https://")):
            raise ImageGenerationTaskError(
                "remote image URLs are not downloaded automatically; use base64 artifacts"
            )
        marker = ";base64,"
        if not raw.startswith("data:image/") or marker not in raw:
            raise ImageGenerationTaskError("unsupported image artifact encoding")
        encoded = raw.split(marker, 1)[1]
        maximum_encoded = ((self._max_bytes + 2) // 3) * 4 + 16
        if len(encoded) > maximum_encoded:
            raise ImageGenerationTaskError("image artifact exceeds configured size limit")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationTaskError("image artifact contains invalid base64") from exc
        if not content:
            raise ImageGenerationTaskError("image artifact is empty")
        if len(content) > self._max_bytes:
            raise ImageGenerationTaskError("image artifact exceeds configured size limit")
        media_type, suffix, width, height = _inspect_image(content)
        if width * height > self._max_pixels:
            raise ImageGenerationTaskError("image artifact exceeds configured pixel limit")
        return content, media_type, suffix, width, height

    @staticmethod
    def _cleanup_written(paths: list[Path]) -> None:
        for path in paths:
            if path.is_file():
                path.unlink()
        directories = {path.parent for path in paths}
        for directory in directories:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()


_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _inspect_image(content: bytes) -> tuple[str, str, int, int]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(content) < 33 or content[12:16] != b"IHDR" or not content.endswith(_PNG_END):
            raise ImageGenerationTaskError("PNG artifact is truncated or malformed")
        width = int.from_bytes(content[16:20], "big")
        height = int.from_bytes(content[20:24], "big")
        if width < 1 or height < 1:
            raise ImageGenerationTaskError("PNG artifact has invalid dimensions")
        return "image/png", ".png", width, height
    if content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"):
        width, height = _jpeg_dimensions(content)
        return "image/jpeg", ".jpg", width, height
    raise ImageGenerationTaskError("only validated PNG and JPEG artifacts are accepted")


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 <= len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        while index < len(content) and content[index] == 0xFF:
            index += 1
        if index >= len(content):
            break
        marker = content[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(content):
            break
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(content[index + 3 : index + 5], "big")
            width = int.from_bytes(content[index + 5 : index + 7], "big")
            if width < 1 or height < 1:
                break
            return width, height
        index += segment_length
    raise ImageGenerationTaskError("JPEG artifact has no valid dimensions")
