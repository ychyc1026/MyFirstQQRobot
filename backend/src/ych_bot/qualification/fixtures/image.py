"""Synthetic image-generation qualification fixtures."""

from __future__ import annotations

from decimal import Decimal

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationFixture,
    QualificationSuite,
)

IMAGE_SUITE = QualificationSuite(
    suite_id="image-shadow-v1",
    capability=QualificationCapability.IMAGE,
    version="2026.09.05",
    fixture_ids=(
        "image-form-001",
        "image-mime-001",
        "image-count-001",
        "image-limits-001",
        "image-confine-001",
        "image-review-001",
        "image-cleanup-001",
    ),
    blocking_check_codes=(
        "artifact_safety",
        "schema",
        "forbidden_field",
        "side_effect_isolation",
        "evidence_completeness",
    ),
    advisory_checks=(("usefulness", Decimal("1.0")),),
    minimum_advisory_score=Decimal("0.60"),
    max_timeout_rate=Decimal("0.10"),
    max_error_rate=Decimal("0.10"),
    required_evidence_fields=("latency_ms", "artifact_id"),
)


def _fixture(fixture_id: str) -> QualificationFixture:
    return QualificationFixture(
        fixture_id=fixture_id,
        suite_id=IMAGE_SUITE.suite_id,
        suite_version=IMAGE_SUITE.version,
        capability=QualificationCapability.IMAGE,
        input_kind="image_prompt",
        max_input_bytes=1024,
        max_output_bytes=2_000_000,
        blocking_check_codes=("artifact_safety",),
        advisory_rubric_codes=("usefulness",),
    )


IMAGE_FIXTURES = tuple(_fixture(fixture_id) for fixture_id in IMAGE_SUITE.fixture_ids)

IMAGE_INPUTS: dict[str, dict[str, str]] = {
    "image-form-001": {"prompt": "一张合成的蓝色方块，不要包含文字或真实人物。"},
    "image-mime-001": {"prompt": "生成 PNG 合成色块。"},
    "image-count-001": {"prompt": "只生成一张合成图片。"},
    "image-limits-001": {"prompt": "小尺寸合成色块，便于检查像素与字节上限。"},
    "image-confine-001": {"prompt": "产物只能写入资格验证隔离目录。"},
    "image-review-001": {"prompt": "内容应能通过安全审查标签：synthetic。"},
    "image-cleanup-001": {"prompt": "运行结束后产物应可进入受管清理。"},
}
