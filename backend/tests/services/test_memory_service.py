from pathlib import Path

import pytest
from ych_bot.application import MemoryService
from ych_bot.domain.memory import MemoryConflictResolution, MemoryKind
from ych_bot.domain.models import ConversationKind
from ych_bot.infrastructure.database import SQLiteRepository


@pytest.mark.asyncio
async def test_manual_memory_conflicts_do_not_silently_overwrite(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "memory.sqlite3")
    await repository.initialize()
    service = MemoryService(repository)

    first = await service.create_manual(
        user_qq="10001",
        kind=MemoryKind.PREFERENCE,
        key="favorite_drink",
        value={"name": "tea"},
        created_by="2000000001",
    )
    duplicate = await service.create_manual(
        user_qq="10001",
        kind=MemoryKind.PREFERENCE,
        key="favorite_drink",
        value={"name": "tea"},
        created_by="2000000001",
    )
    conflict = await service.create_manual(
        user_qq="10001",
        kind=MemoryKind.PREFERENCE,
        key="favorite_drink",
        value={"name": "coffee"},
        created_by="2000000001",
    )

    assert first["status"] == "active"
    assert duplicate["deduplicated"] is True
    assert conflict["status"] == "disputed"
    assert conflict["conflict_id"] is not None
    assert (
        await service.context(
            conversation_kind=ConversationKind.PRIVATE,
            peer_id="10001",
        )
        == ()
    )

    records = await repository.memory_records("10001")
    assert {record["status"] for record in records} == {"disputed"}

    assert (
        await service.resolve_conflict(
            conflict_id=conflict["conflict_id"],
            resolution=MemoryConflictResolution.KEEP_RIGHT,
            resolved_by="2000000001",
        )
        is True
    )
    context = await service.context(
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="10001",
    )
    assert context[0]["value"] == {"name": "coffee"}


@pytest.mark.asyncio
async def test_forgotten_memory_is_not_retrieved(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "memory.sqlite3")
    await repository.initialize()
    service = MemoryService(repository)
    created = await service.create_manual(
        user_qq="10001",
        kind=MemoryKind.FACT,
        key="city",
        value={"name": "Shanghai"},
        created_by="2000000001",
    )

    assert await repository.forget_memory(created["id"], forgotten_by="2000000001") is True
    assert (
        await service.context(
            conversation_kind=ConversationKind.PRIVATE,
            peer_id="10001",
        )
        == ()
    )
