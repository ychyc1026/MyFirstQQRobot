from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.application import (
    ContextAssemblyError,
    ConversationContextService,
    MemoryService,
    PersonaService,
    ProvenanceContextAssembler,
    ReplyOrchestrationService,
)
from ych_bot.domain.identity import HistoryAccessMode
from ych_bot.domain.memory import MemoryKind
from ych_bot.domain.persona import ProfileScope
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_QQ = "123456789"


def event(message_id: int, *, user_qq: str, occurred_at: datetime) -> dict[str, object]:
    return {
        "time": int(occurred_at.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": int(user_qq),
        "message": [{"type": "text", "data": {"text": f"message-{message_id}"}}],
    }


def group_event(
    message_id: int,
    *,
    user_qq: str,
    group_qq: str,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "time": int(occurred_at.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "user_id": int(user_qq),
        "group_id": int(group_qq),
        "message": [{"type": "text", "data": {"text": f"group-{message_id}"}}],
    }


async def store_message(
    repository: SQLiteRepository,
    message_id: int,
    *,
    user_qq: str,
    occurred_at: datetime,
):
    message = parse_message_event(
        event(message_id, user_qq=user_qq, occurred_at=occurred_at),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(message)
    return message


def assembler(repository: SQLiteRepository) -> ProvenanceContextAssembler:
    return ProvenanceContextAssembler(
        repository,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
    )


async def claimed_private_run(
    repository: SQLiteRepository,
    *,
    now: datetime,
    user_qq: str = USER_QQ,
) -> dict:
    trigger = await store_message(
        repository,
        902,
        user_qq=user_qq,
        occurred_at=now,
    )
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    registered = await orchestration.register_message(trigger, observed_at=now)
    claimed = await orchestration.claim_ready(worker_id="context-worker", now=now)
    assert claimed is not None
    assert claimed["id"] == registered["run"]["id"]
    return claimed


async def claimed_group_run(
    repository: SQLiteRepository,
    *,
    now: datetime,
    user_qq: str,
    group_qq: str,
) -> dict:
    trigger = parse_message_event(
        group_event(903, user_qq=user_qq, group_qq=group_qq, occurred_at=now),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(trigger)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    registered = await orchestration.register_message(trigger, observed_at=now)
    claimed = await orchestration.claim_ready(worker_id="group-context-worker", now=now)
    assert claimed is not None
    assert claimed["id"] == registered["run"]["id"]
    return claimed


@pytest.mark.asyncio
async def test_private_context_manifest_records_sources_and_allowed_history(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "context-assembly.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    old_message = await store_message(
        repository,
        901,
        user_qq=USER_QQ,
        occurred_at=now - timedelta(minutes=5),
    )
    persona = await PersonaService(repository).save_manual(
        scope=ProfileScope.PRIVATE_USER,
        user_qq=USER_QQ,
        name="私人沟通风格",
        definition="语气自然，回答简洁。",
        created_by=OWNER_QQ,
    )
    memory = await MemoryService(repository).create_manual(
        user_qq=USER_QQ,
        kind=MemoryKind.PREFERENCE,
        key="reply_length",
        value={"preference": "short"},
        created_by=OWNER_QQ,
    )
    await repository.set_history_policy(
        user_qq=USER_QQ,
        mode=HistoryAccessMode.SELECTED_RANGE,
        selected_from=(now.date() - timedelta(days=1)).isoformat(),
        selected_to=(now.date() + timedelta(days=1)).isoformat(),
        max_messages=20,
        updated_by=OWNER_QQ,
        reason="owner selected a local history range",
    )
    claimed = await claimed_private_run(repository, now=now)

    manifest = await assembler(repository).assemble(
        run_id=claimed["id"],
        lease_token=claimed["lease"]["lease_token"],
        actor_qq=USER_QQ,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    sections = {section.section_id: section for section in manifest.sections}
    assert tuple(sections) == (
        "core-identity",
        "authorization",
        "trigger-conversation",
        "persona",
        "knowledge",
        "memory",
        "history",
    )
    assert sections["persona"].record_ids == (persona["id"],)
    assert sections["memory"].record_ids == (memory["id"],)
    assert sections["history"].record_ids == (old_message.id,)
    assert json.loads(sections["history"].content)[0]["plain_text"] == "message-901"
    trigger_records = sections["trigger-conversation"].record_ids
    assert len(trigger_records) == 1
    trigger_id = trigger_records[0]
    assert json.loads(sections["trigger-conversation"].content)[0]["plain_text"] == ("message-902")
    assert trigger_id not in sections["history"].record_ids
    assert sections["authorization"].policy_reason == "platform_identity_and_stored_policy"

    saved = await repository.reply_run_detail(claimed["id"])
    assert saved is not None
    assert len(saved["context_manifests"]) == 1
    assert saved["context_manifests"][0]["budget_chars"] == 12_000
    assert saved["context_manifests"][0]["used_chars"] > 0
    assert saved["context_manifests"][0]["rendered_sha256"]
    stored_sections = saved["context_manifests"][0]["manifest"]["sections"]
    assert stored_sections[6]["policy_reason"] == "selected_range_policy"
    assert stored_sections[6]["record_ids"] == [old_message.id]


@pytest.mark.asyncio
async def test_actor_mismatch_denies_private_layers_without_leaking_record_ids(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "context-isolation.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    private_persona = await PersonaService(repository).save_manual(
        scope=ProfileScope.PRIVATE_USER,
        user_qq=USER_QQ,
        name="private",
        definition="secret persona",
        created_by=OWNER_QQ,
    )
    await MemoryService(repository).create_manual(
        user_qq=USER_QQ,
        kind=MemoryKind.FACT,
        key="secret",
        value={"value": "hidden"},
        created_by=OWNER_QQ,
    )
    claimed = await claimed_private_run(repository, now=now)

    manifest = await assembler(repository).assemble(
        run_id=claimed["id"],
        lease_token=claimed["lease"]["lease_token"],
        actor_qq="999999999",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    sections = {section.section_id: section for section in manifest.sections}
    persona_content = json.loads(sections["persona"].content)
    assert persona_content["private_definition"] is None
    assert private_persona["id"] not in sections["persona"].record_ids
    for section_id in ("knowledge", "memory", "history"):
        section = sections[section_id]
        assert section.policy_decision.value == "denied"
        assert section.content == ""
        assert section.record_ids == ()
        assert section.policy_reason == "group_or_actor_mismatch"


@pytest.mark.asyncio
async def test_context_assembly_rejects_expired_or_wrong_lease(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "context-lease.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    claimed = await claimed_private_run(repository, now=now)
    service = assembler(repository)

    with pytest.raises(ContextAssemblyError, match="does not match"):
        await service.assemble(
            run_id=claimed["id"],
            lease_token="wrong-token",
            actor_qq=USER_QQ,
            owner_qq=OWNER_QQ,
            bot_qq=BOT_QQ,
            now=now,
        )
    with pytest.raises(ContextAssemblyError, match="expired"):
        await service.assemble(
            run_id=claimed["id"],
            lease_token=claimed["lease"]["lease_token"],
            actor_qq=USER_QQ,
            owner_qq=OWNER_QQ,
            bot_qq=BOT_QQ,
            now=now + timedelta(seconds=31),
        )

    detail = await repository.reply_run_detail(claimed["id"])
    assert detail is not None
    assert detail["context_manifests"] == []


@pytest.mark.asyncio
async def test_group_assembly_denies_known_members_private_material(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "context-group.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    private_persona = await PersonaService(repository).save_manual(
        scope=ProfileScope.PRIVATE_USER,
        user_qq=USER_QQ,
        name="private",
        definition="private-only instructions",
        created_by=OWNER_QQ,
    )
    private_memory = await MemoryService(repository).create_manual(
        user_qq=USER_QQ,
        kind=MemoryKind.FACT,
        key="private_fact",
        value={"value": "private-only"},
        created_by=OWNER_QQ,
    )
    claimed = await claimed_group_run(
        repository,
        now=now,
        user_qq=USER_QQ,
        group_qq="20001",
    )

    context = await assembler(repository).assemble(
        run_id=claimed["id"],
        lease_token=claimed["lease"]["lease_token"],
        actor_qq=USER_QQ,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    sections = {item.section_id: item for item in context.sections}
    assert json.loads(sections["persona"].content)["private_definition"] is None
    assert private_persona["id"] not in sections["persona"].record_ids
    for section_id in ("knowledge", "memory", "history"):
        section = sections[section_id]
        assert section.policy_decision.value == "denied"
        assert section.scope.value == "group"
        assert section.subject_qq is None
        assert section.record_ids == ()
    assert private_memory["id"] not in {
        record_id for section in context.sections for record_id in section.record_ids
    }
