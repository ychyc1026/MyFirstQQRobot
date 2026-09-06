from datetime import UTC, datetime, timedelta

from ych_bot.domain.memory import (
    MemoryKind,
    MemoryRecord,
    MemorySource,
    MemoryStatus,
    retrievable_memories,
)
from ych_bot.domain.models import ConversationKind


def memory(
    *,
    memory_id: str,
    user_qq: str,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        user_qq=user_qq,
        kind=MemoryKind.PREFERENCE,
        key="favorite_drink",
        value={"name": "tea"},
        source=MemorySource.OWNER_MANUAL,
        confidence=1.0,
        status=status,
        expires_at=expires_at,
    )


def test_memory_retrieval_is_private_user_isolated() -> None:
    now = datetime.now(UTC)
    memories = (
        memory(memory_id="a", user_qq="10001"),
        memory(memory_id="b", user_qq="10002"),
        memory(memory_id="c", user_qq="10001", status=MemoryStatus.CANDIDATE),
        memory(memory_id="d", user_qq="10001", expires_at=now - timedelta(seconds=1)),
    )

    result = retrievable_memories(
        memories,
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="10001",
        now=now,
    )
    assert tuple(item.id for item in result) == ("a",)


def test_group_never_receives_personal_memory() -> None:
    result = retrievable_memories(
        (memory(memory_id="a", user_qq="10001"),),
        conversation_kind=ConversationKind.GROUP,
        peer_id="10001",
    )
    assert result == ()
