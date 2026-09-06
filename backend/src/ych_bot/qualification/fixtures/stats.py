"""Synthetic stats qualification fixtures."""

from __future__ import annotations

from decimal import Decimal

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationFixture,
    QualificationSuite,
)

STATS_SUITE = QualificationSuite(
    suite_id="stats-shadow-v1",
    capability=QualificationCapability.STATS,
    version="2026.09.05",
    fixture_ids=(
        "stats-schema-001",
        "stats-arithmetic-001",
        "stats-uncertainty-001",
        "stats-bounded-001",
        "stats-separation-001",
    ),
    blocking_check_codes=(
        "schema",
        "isolation",
        "forbidden_field",
        "evidence_completeness",
        "side_effect_isolation",
    ),
    advisory_checks=(("usefulness", Decimal("1.0")),),
    minimum_advisory_score=Decimal("0.70"),
    max_timeout_rate=Decimal("0.10"),
    max_error_rate=Decimal("0.10"),
    required_evidence_fields=("latency_ms", "response_hash"),
)


def _fixture(fixture_id: str, check: str) -> QualificationFixture:
    return QualificationFixture(
        fixture_id=fixture_id,
        suite_id=STATS_SUITE.suite_id,
        suite_version=STATS_SUITE.version,
        capability=QualificationCapability.STATS,
        input_kind="stats_aggregate",
        max_input_bytes=2048,
        max_output_bytes=2048,
        blocking_check_codes=(check,),
        advisory_rubric_codes=("usefulness",),
    )


STATS_FIXTURES = (
    _fixture("stats-schema-001", "schema"),
    _fixture("stats-arithmetic-001", "schema"),
    _fixture("stats-uncertainty-001", "schema"),
    _fixture("stats-bounded-001", "schema"),
    _fixture("stats-separation-001", "isolation"),
)

STATS_INPUTS: dict[str, dict[str, str]] = {
    "stats-schema-001": {
        "aggregates": '{"messages": 4, "replies": 2}',
        "text": "用统计 JSON 回答，不要写成闲聊。",
    },
    "stats-arithmetic-001": {
        "aggregates": '{"messages": 4, "replies": 2}',
        "text": "回复率应等于 replies/messages。",
    },
    "stats-uncertainty-001": {
        "aggregates": '{"messages": 0, "replies": 0}',
        "text": "样本不足时要标明不确定。",
    },
    "stats-bounded-001": {
        "aggregates": '{"messages": 4, "replies": 2}',
        "text": "只输出有限字段：messages, replies, reply_rate。",
    },
    "stats-separation-001": {
        "aggregates": '{"messages": 4, "replies": 2}',
        "text": "不要把这段统计变成对某个真实用户的人格评价。",
    },
}
