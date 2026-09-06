"""Image-generation task states shared by dashboard and owner control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ImageTaskStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_MODEL_CONFIG = "awaiting_model_config"
    GENERATING = "generating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPROVAL_EXPIRED = "approval_expired"
    REVIEW_BLOCKED = "review_blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImageIntendedUse(StrEnum):
    GENERAL = "general"
    PRIVATE_REPLY = "private_reply"
    QZONE_POST = "qzone_post"
    PROFILE_ASSET = "profile_asset"


@dataclass(frozen=True, slots=True)
class ImageReviewResult:
    verdict: str
    labels: tuple[str, ...]
    engine: str


class ImageReviewEngine(Protocol):
    def review(
        self,
        *,
        content: bytes,
        media_type: str,
        content_sha256: str,
    ) -> ImageReviewResult: ...
