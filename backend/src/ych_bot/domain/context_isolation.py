"""Fail-closed validation for context before prompt rendering."""

from __future__ import annotations

from .models import ConversationKind
from .reply_pipeline import (
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
)


class ContextIsolationError(ValueError):
    """A context manifest crosses its declared conversation boundary."""


PRIVATE_SOURCE_CLASSES = frozenset(
    {
        ContextSourceClass.USER_UNDERSTANDING,
        ContextSourceClass.KNOWLEDGE,
        ContextSourceClass.MEMORY,
        ContextSourceClass.HISTORY,
    }
)


def validate_context_isolation(
    manifest: ContextManifest,
    *,
    conversation_kind: ConversationKind,
    peer_id: str,
) -> None:
    """Reject cross-user or private-in-group sections without reading their content."""

    _validate_conversation_key(manifest, conversation_kind, peer_id)
    seen: set[str] = set()
    for section in manifest.sections:
        if section.section_id in seen:
            raise ContextIsolationError("context section IDs must be unique")
        seen.add(section.section_id)
        if section.scope in {ContextScope.GLOBAL, ContextScope.GROUP} and section.subject_qq:
            raise ContextIsolationError("global and group context cannot name a user subject")

        if conversation_kind is ConversationKind.GROUP:
            _validate_group_section(section)
            continue

        if section.subject_qq is not None and section.subject_qq != peer_id:
            raise ContextIsolationError("private context names a different user")
        if section.source_class in PRIVATE_SOURCE_CLASSES and (
            section.scope is not ContextScope.USER or section.subject_qq != peer_id
        ):
            raise ContextIsolationError("private material must belong to the private peer")


def _validate_conversation_key(
    manifest: ContextManifest,
    conversation_kind: ConversationKind,
    peer_id: str,
) -> None:
    parts = manifest.conversation_key.split(":", maxsplit=2)
    if (
        len(parts) != 3
        or not parts[0].isdigit()
        or parts[1] != conversation_kind.value
        or parts[2] != peer_id
    ):
        raise ContextIsolationError("context manifest conversation key does not match the run")


def _validate_group_section(section: ContextSection) -> None:
    if section.scope is ContextScope.USER:
        raise ContextIsolationError("group context cannot contain user-scoped sections")
    if section.source_class not in PRIVATE_SOURCE_CLASSES:
        return
    if section.policy_decision is not ContextPolicyDecision.DENIED:
        raise ContextIsolationError("group context must deny private source classes")
    if section.scope is not ContextScope.GROUP or section.subject_qq or section.record_ids:
        raise ContextIsolationError("denied group-private sections must not expose user references")
