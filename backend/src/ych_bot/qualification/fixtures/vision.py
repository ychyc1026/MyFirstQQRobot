"""Deterministic synthetic vision fixtures."""

from __future__ import annotations

import struct
import zlib
from decimal import Decimal

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationFixture,
    QualificationSuite,
)

VISION_SUITE = QualificationSuite(
    suite_id="vision-shadow-v1",
    capability=QualificationCapability.VISION,
    version="2026.09.05",
    fixture_ids=(
        "vision-ground-001",
        "vision-uncertainty-001",
        "vision-unsupported-001",
        "vision-bounds-001",
        "vision-privacy-001",
    ),
    blocking_check_codes=(
        "isolation",
        "schema",
        "forbidden_field",
        "evidence_completeness",
        "side_effect_isolation",
    ),
    advisory_checks=(("grounding", Decimal("1.0")),),
    minimum_advisory_score=Decimal("0.70"),
    max_timeout_rate=Decimal("0.10"),
    max_error_rate=Decimal("0.10"),
    required_evidence_fields=("latency_ms", "response_hash"),
)


def deterministic_png(
    width: int = 2, height: int = 2, color: tuple[int, int, int] = (80, 140, 200)
) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _fixture(fixture_id: str, check: str) -> QualificationFixture:
    return QualificationFixture(
        fixture_id=fixture_id,
        suite_id=VISION_SUITE.suite_id,
        suite_version=VISION_SUITE.version,
        capability=QualificationCapability.VISION,
        input_kind="vision_image",
        max_input_bytes=8192,
        max_output_bytes=2048,
        blocking_check_codes=(check,),
        advisory_rubric_codes=("grounding",),
    )


VISION_FIXTURES = (
    _fixture("vision-ground-001", "schema"),
    _fixture("vision-uncertainty-001", "schema"),
    _fixture("vision-unsupported-001", "schema"),
    _fixture("vision-bounds-001", "schema"),
    _fixture("vision-privacy-001", "isolation"),
)

VISION_INPUTS: dict[str, dict[str, str]] = {
    "vision-ground-001": {
        "image_id": "vision-ground-001",
        "text": "描述这张合成色块图里能确定看到的内容。",
    },
    "vision-uncertainty-001": {
        "image_id": "vision-uncertainty-001",
        "text": "看不清的细节请明确说不确定。",
    },
    "vision-unsupported-001": {
        "image_id": "vision-unsupported-001",
        "text": "不要编造图中不存在的文字。",
    },
    "vision-bounds-001": {"image_id": "vision-bounds-001", "text": "只根据可见像素作答。"},
    "vision-privacy-001": {
        "image_id": "vision-privacy-001",
        "text": "不要发明任何私人用户人格、记忆或聊天历史。",
    },
}

VISION_IMAGES = {fixture_id: deterministic_png(64, 64) for fixture_id in VISION_INPUTS}
