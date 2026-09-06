"""Assemble inspectable prompt layers without calling a model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ych_bot.domain.models import ConversationKind
from ych_bot.domain.system_identity import CORE_IDENTITY

from .memory import MemoryService
from .personas import PersonaService


@dataclass(frozen=True, slots=True)
class ConversationContext:
    core_identity: dict[str, Any]
    authenticated_creator: bool
    core_directives: tuple[str, ...]
    persona: dict[str, Any]
    user_reference: dict[str, Any] | None
    memories: tuple[dict[str, Any], ...]
    isolation: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConversationContextService:
    def __init__(
        self,
        persona_service: PersonaService,
        memory_service: MemoryService,
    ) -> None:
        self._persona_service = persona_service
        self._memory_service = memory_service

    async def assemble(
        self,
        *,
        conversation_kind: ConversationKind,
        peer_id: str,
        actor_qq: str,
        owner_qq: str | None = None,
        bot_qq: str | None = None,
    ) -> ConversationContext:
        owner = owner_qq or CORE_IDENTITY.creator_qq
        bot = bot_qq or CORE_IDENTITY.bot_qq
        persona = await self._persona_service.context(
            conversation_kind=conversation_kind,
            peer_id=peer_id,
        )
        memories = await self._memory_service.context(
            conversation_kind=conversation_kind,
            peer_id=peer_id,
        )
        private_allowed = conversation_kind is ConversationKind.PRIVATE and actor_qq == peer_id
        if not private_allowed:
            memories = ()
            user_reference = None
            private_definition = None
            derived_persona = None
            applied_profile_ids = ()
        else:
            user_reference = persona.user_context
            private_definition = persona.private_definition
            derived_persona = persona.derived_persona
            applied_profile_ids = persona.applied_profile_ids

        return ConversationContext(
            core_identity=CORE_IDENTITY.as_dict(),
            authenticated_creator=actor_qq == owner,
            core_directives=(
                *CORE_IDENTITY.prompt_directives(owner_qq=owner, bot_qq=bot),
                *CORE_IDENTITY.conversation_directives(actor_qq, owner_qq=owner),
            ),
            persona={
                "base_definition": persona.base_definition,
                "private_definition": private_definition,
                "derived_persona": derived_persona,
                "applied_profile_ids": applied_profile_ids,
            },
            user_reference=user_reference,
            memories=memories,
            isolation={
                "private_user_layers_allowed": private_allowed,
                "user_reference_is_instruction": False,
                "memory_is_instruction": False,
                "core_identity_priority": 0,
            },
        )
