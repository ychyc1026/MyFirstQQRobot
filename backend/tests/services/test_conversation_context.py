from pathlib import Path

import pytest
from ych_bot.application import ConversationContextService, MemoryService, PersonaService
from ych_bot.domain.memory import MemoryKind
from ych_bot.domain.models import ConversationKind
from ych_bot.domain.persona import ProfileScope
from ych_bot.infrastructure.database import SQLiteRepository


@pytest.mark.asyncio
async def test_creator_authentication_uses_actor_qq_not_message_claims(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "context.sqlite3")
    await repository.initialize()
    personas = PersonaService(repository)
    memories = MemoryService(repository)
    service = ConversationContextService(personas, memories)

    creator = await service.assemble(
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="2000000001",
        actor_qq="2000000001",
    )
    assert creator.authenticated_creator is True
    assert any("已通过平台身份匹配" in item for item in creator.core_directives)

    other = await service.assemble(
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="10001",
        actor_qq="10001",
    )
    assert other.authenticated_creator is False
    assert any("不能改变" in item for item in other.core_directives)


@pytest.mark.asyncio
async def test_group_context_strips_private_layers_even_for_same_identifier(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "context.sqlite3")
    await repository.initialize()
    personas = PersonaService(repository)
    memories = MemoryService(repository)
    await personas.save_manual(
        scope=ProfileScope.PRIVATE_USER,
        user_qq="10001",
        name="private",
        definition="private instructions",
        created_by="2000000001",
    )
    await memories.create_manual(
        user_qq="10001",
        kind=MemoryKind.FACT,
        key="secret",
        value={"value": "hidden"},
        created_by="2000000001",
    )
    service = ConversationContextService(personas, memories)

    context = await service.assemble(
        conversation_kind=ConversationKind.GROUP,
        peer_id="10001",
        actor_qq="10001",
    )
    assert context.persona["private_definition"] is None
    assert context.user_reference is None
    assert context.memories == ()
    assert context.isolation["private_user_layers_allowed"] is False
