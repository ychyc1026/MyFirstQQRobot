"""Persona administration and isolated context assembly."""

from __future__ import annotations

from uuid import uuid4

from ych_bot.domain.models import ConversationKind
from ych_bot.domain.persona import PersonaContext, ProfileScope, build_persona_context
from ych_bot.infrastructure.database import SQLiteRepository


class PersonaValidationError(ValueError):
    pass


class PersonaService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    async def save_manual(
        self,
        *,
        scope: ProfileScope,
        user_qq: str | None,
        name: str,
        definition: str,
        created_by: str,
    ) -> dict[str, object]:
        normalized_name = name.strip()
        normalized_definition = definition.strip()
        if not normalized_name or len(normalized_name) > 80:
            raise PersonaValidationError("name must contain 1 to 80 characters")
        if not normalized_definition or len(normalized_definition) > 1000:
            raise PersonaValidationError("definition must contain 1 to 1000 characters")
        if scope is ProfileScope.GLOBAL:
            scope_id = "*"
        else:
            scope_id = (user_qq or "").strip()
            if not scope_id.isdigit():
                raise PersonaValidationError("private_user scope requires a numeric user_qq")

        profile = await self._repository.save_manual_persona(
            profile_id=str(uuid4()),
            scope=scope,
            scope_id=scope_id,
            name=normalized_name,
            developer_definition=normalized_definition,
            created_by=created_by,
        )
        return {
            "id": profile.id,
            "scope": profile.scope.value,
            "scope_id": profile.scope_id,
            "source": profile.source.value,
            "version": profile.version,
        }

    async def context(
        self,
        *,
        conversation_kind: ConversationKind,
        peer_id: str,
    ) -> PersonaContext:
        profiles, understandings = await self._repository.active_persona_inputs(peer_id)
        return build_persona_context(
            conversation_kind=conversation_kind,
            peer_id=peer_id,
            profiles=profiles,
            understandings=understandings,
        )
