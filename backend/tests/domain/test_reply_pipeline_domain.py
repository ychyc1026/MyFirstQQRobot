from datetime import UTC, datetime

import pytest
from ych_bot.domain import (
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    ConversationKind,
    MessageDirection,
    MessageEnvelope,
    MessageSegment,
    ReplyBubble,
    ReplyFailure,
    ReplyFailureCategory,
    ReplyPlan,
    ReplyRunStage,
    UnifiedMessage,
    can_transition_reply_stage,
)
from ych_bot.domain.reply_pipeline import REPLY_STAGE_TRANSITIONS


def test_message_envelope_preserves_conversation_identity_and_segments() -> None:
    message = UnifiedMessage(
        id="message-1",
        event_key="event-1",
        source_message_id="100",
        bot_qq="2000000002",
        direction=MessageDirection.INBOUND,
        conversation_kind=ConversationKind.PRIVATE,
        conversation_id="123456789",
        sender_id="123456789",
        occurred_at=datetime(2026, 8, 28, tzinfo=UTC),
        segments=(MessageSegment(type="text", data={"text": "你好"}),),
        raw_event={"post_type": "message"},
    )

    envelope = MessageEnvelope.from_unified(message)

    assert envelope.conversation_key == "2000000002:private:123456789"
    assert envelope.segments == message.segments
    assert envelope.event_key == "event-1"


def test_reply_stage_transitions_are_explicit_and_terminal() -> None:
    assert can_transition_reply_stage(ReplyRunStage.PENDING, ReplyRunStage.SETTLING)
    assert not can_transition_reply_stage(ReplyRunStage.PENDING, ReplyRunStage.CALLING_MODEL)
    assert ReplyRunStage.COMPLETED.terminal is True
    assert not can_transition_reply_stage(ReplyRunStage.COMPLETED, ReplyRunStage.PENDING)


def test_reply_stage_transition_matrix_matches_declared_graph() -> None:
    stages = tuple(ReplyRunStage)

    for current in stages:
        for target in stages:
            assert can_transition_reply_stage(current, target) is (
                target in REPLY_STAGE_TRANSITIONS[current]
            )

    assert set(REPLY_STAGE_TRANSITIONS) == set(stages)
    assert all(not REPLY_STAGE_TRANSITIONS[stage] for stage in stages if stage.terminal)


def test_context_manifest_separates_included_and_denied_evidence() -> None:
    included = ContextSection(
        section_id="identity",
        source_class=ContextSourceClass.CORE_IDENTITY,
        scope=ContextScope.GLOBAL,
        content="YCH",
        policy_decision=ContextPolicyDecision.ALLOWED,
    )
    denied = ContextSection(
        section_id="history",
        source_class=ContextSourceClass.HISTORY,
        scope=ContextScope.USER,
        subject_qq="123456789",
        content="",
        policy_decision=ContextPolicyDecision.DENIED,
        record_ids=("grant-missing",),
    )

    manifest = ContextManifest("run-1", "2000000002:private:123456789", (included, denied))

    assert manifest.included == (included,)
    assert manifest.denied == (denied,)


def test_context_section_rejects_private_or_denied_content_without_boundary() -> None:
    with pytest.raises(ValueError, match="subject_qq"):
        ContextSection(
            section_id="memory",
            source_class=ContextSourceClass.MEMORY,
            scope=ContextScope.USER,
            content="fact",
            policy_decision=ContextPolicyDecision.ALLOWED,
        )
    with pytest.raises(ValueError, match="denied context"):
        ContextSection(
            section_id="history",
            source_class=ContextSourceClass.HISTORY,
            scope=ContextScope.USER,
            subject_qq="123456789",
            content="must not leak",
            policy_decision=ContextPolicyDecision.DENIED,
        )


def test_reply_plan_requires_ordered_unique_idempotent_bubbles() -> None:
    first = ReplyBubble(1, "run-1:1", (MessageSegment("text", {"text": "你好"}),))
    second = ReplyBubble(2, "run-1:2", (MessageSegment("text", {"text": "今天怎么样"}),))
    assert ReplyPlan("run-1", (first, second)).bubbles == (first, second)

    with pytest.raises(ValueError, match="contiguous"):
        ReplyPlan("run-1", (second,))
    with pytest.raises(ValueError, match="unique"):
        ReplyPlan("run-1", (first, ReplyBubble(2, "run-1:1", first.segments)))


def test_reply_failure_contains_safe_machine_readable_taxonomy() -> None:
    failure = ReplyFailure(
        code="onebot_delivery_ambiguous",
        category=ReplyFailureCategory.DELIVERY_UNKNOWN,
        retryable=False,
        safe_detail="transport closed before acknowledgement",
    )

    assert failure.category is ReplyFailureCategory.DELIVERY_UNKNOWN
    assert failure.retryable is False
