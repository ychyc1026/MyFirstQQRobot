from __future__ import annotations

import pytest
from ych_bot.domain import (
    ContextIsolationError,
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    ConversationKind,
    validate_context_isolation,
)


def section(
    *,
    section_id: str = "memory",
    source: ContextSourceClass = ContextSourceClass.MEMORY,
    scope: ContextScope = ContextScope.USER,
    subject_qq: str | None = "10001",
    decision: ContextPolicyDecision = ContextPolicyDecision.ALLOWED,
    record_ids: tuple[str, ...] = ("memory-1",),
) -> ContextSection:
    return ContextSection(
        section_id=section_id,
        source_class=source,
        scope=scope,
        subject_qq=subject_qq,
        content="{}" if decision is ContextPolicyDecision.ALLOWED else "",
        policy_decision=decision,
        record_ids=record_ids,
        policy_reason="test_policy",
    )


def manifest(*sections: ContextSection, key: str = "2000000002:private:10001"):
    return ContextManifest(run_id="run-1", conversation_key=key, sections=sections)


def test_private_manifest_accepts_only_the_conversation_peer() -> None:
    valid = manifest(section())
    validate_context_isolation(
        valid,
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="10001",
    )

    crossed = manifest(section(subject_qq="10002"))
    with pytest.raises(ContextIsolationError, match="different user"):
        validate_context_isolation(
            crossed,
            conversation_kind=ConversationKind.PRIVATE,
            peer_id="10001",
        )


def test_group_manifest_rejects_allowed_private_source() -> None:
    crossed = manifest(
        section(scope=ContextScope.CONVERSATION, subject_qq=None),
        key="2000000002:group:20001",
    )
    with pytest.raises(ContextIsolationError, match="deny private"):
        validate_context_isolation(
            crossed,
            conversation_kind=ConversationKind.GROUP,
            peer_id="20001",
        )


def test_group_denial_cannot_expose_private_record_references() -> None:
    leaked = manifest(
        section(
            scope=ContextScope.GROUP,
            subject_qq=None,
            decision=ContextPolicyDecision.DENIED,
        ),
        key="2000000002:group:20001",
    )
    with pytest.raises(ContextIsolationError, match="must not expose"):
        validate_context_isolation(
            leaked,
            conversation_kind=ConversationKind.GROUP,
            peer_id="20001",
        )

    safe = manifest(
        section(
            scope=ContextScope.GROUP,
            subject_qq=None,
            decision=ContextPolicyDecision.DENIED,
            record_ids=(),
        ),
        key="2000000002:group:20001",
    )
    validate_context_isolation(
        safe,
        conversation_kind=ConversationKind.GROUP,
        peer_id="20001",
    )


def test_manifest_rejects_duplicate_sections_and_wrong_conversation_key() -> None:
    duplicate = manifest(section(), section())
    with pytest.raises(ContextIsolationError, match="unique"):
        validate_context_isolation(
            duplicate,
            conversation_kind=ConversationKind.PRIVATE,
            peer_id="10001",
        )

    wrong_key = manifest(section(), key="2000000002:private:10002")
    with pytest.raises(ContextIsolationError, match="conversation key"):
        validate_context_isolation(
            wrong_key,
            conversation_kind=ConversationKind.PRIVATE,
            peer_id="10001",
        )
