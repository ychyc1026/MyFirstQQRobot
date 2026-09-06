"""Persona isolation rules for global, private-user, and imported knowledge layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import ConversationKind
from .system_identity import CORE_IDENTITY, CoreIdentity


class ProfileScope(StrEnum):
    GLOBAL = "global"
    PRIVATE_USER = "private_user"


class ProfileSource(StrEnum):
    MANUAL = "manual"
    DOCUMENT_DERIVED = "document_derived"


class DocumentPurpose(StrEnum):
    USER_UNDERSTANDING = "user_understanding"
    PERSONA_DESIGN = "persona_design"


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    id: str
    scope: ProfileScope
    scope_id: str
    source: ProfileSource
    developer_definition: str
    traits: dict[str, Any]
    version: int = 1


@dataclass(frozen=True, slots=True)
class UserUnderstanding:
    id: str
    user_qq: str
    summary: dict[str, Any]
    evidence: tuple[dict[str, Any], ...] = ()
    version: int = 1


@dataclass(frozen=True, slots=True)
class PersonaContext:
    core_identity: CoreIdentity
    core_directives: tuple[str, ...]
    base_definition: str | None
    private_definition: str | None
    derived_persona: dict[str, Any] | None
    user_context: dict[str, Any] | None
    applied_profile_ids: tuple[str, ...]


def build_persona_context(
    *,
    conversation_kind: ConversationKind,
    peer_id: str,
    profiles: tuple[PersonaProfile, ...],
    understandings: tuple[UserUnderstanding, ...],
) -> PersonaContext:
    """Build isolated prompt inputs without treating user facts as persona instructions."""
    global_manual = _latest_profile(
        profiles,
        scope=ProfileScope.GLOBAL,
        scope_id="*",
        source=ProfileSource.MANUAL,
    )
    applied = [global_manual.id] if global_manual else []

    if conversation_kind is not ConversationKind.PRIVATE:
        return PersonaContext(
            core_identity=CORE_IDENTITY,
            core_directives=CORE_IDENTITY.prompt_directives(),
            base_definition=global_manual.developer_definition if global_manual else None,
            private_definition=None,
            derived_persona=None,
            user_context=None,
            applied_profile_ids=tuple(applied),
        )

    private_manual = _latest_profile(
        profiles,
        scope=ProfileScope.PRIVATE_USER,
        scope_id=peer_id,
        source=ProfileSource.MANUAL,
    )
    private_derived = _latest_profile(
        profiles,
        scope=ProfileScope.PRIVATE_USER,
        scope_id=peer_id,
        source=ProfileSource.DOCUMENT_DERIVED,
    )
    understanding = _latest_understanding(understandings, peer_id)
    for item in (private_derived, private_manual):
        if item:
            applied.append(item.id)

    return PersonaContext(
        core_identity=CORE_IDENTITY,
        core_directives=CORE_IDENTITY.prompt_directives(),
        base_definition=global_manual.developer_definition if global_manual else None,
        private_definition=(private_manual.developer_definition if private_manual else None),
        derived_persona=private_derived.traits if private_derived else None,
        user_context=understanding.summary if understanding else None,
        applied_profile_ids=tuple(applied),
    )


def _latest_profile(
    profiles: tuple[PersonaProfile, ...],
    *,
    scope: ProfileScope,
    scope_id: str,
    source: ProfileSource,
) -> PersonaProfile | None:
    matches = (
        profile
        for profile in profiles
        if profile.scope is scope and profile.scope_id == scope_id and profile.source is source
    )
    return max(matches, key=lambda profile: profile.version, default=None)


def _latest_understanding(
    understandings: tuple[UserUnderstanding, ...],
    user_qq: str,
) -> UserUnderstanding | None:
    matches = (item for item in understandings if item.user_qq == user_qq)
    return max(matches, key=lambda item: item.version, default=None)
