from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.application import (
    ConversationContextService,
    MemoryService,
    PersonaService,
    ProvenanceContextAssembler,
    ReplyOrchestrationService,
)
from ych_bot.domain import ReplyFailure, ReplyFailureCategory, ReplyRunStage
from ych_bot.domain.identity import HistoryAccessMode
from ych_bot.domain.memory import MemoryKind
from ych_bot.domain.persona import ProfileScope
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_A = "111111111"
USER_B = "222222222"
GROUP_QQ = "333333333"


def private_event(message_id: int, user_qq: str, occurred_at: datetime) -> dict:
    return {
        "time": int(occurred_at.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": int(user_qq),
        "message": [{"type": "text", "data": {"text": f"private-{message_id}"}}],
    }


def group_event(message_id: int, user_qq: str, occurred_at: datetime) -> dict:
    return {
        "time": int(occurred_at.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "user_id": int(user_qq),
        "group_id": int(GROUP_QQ),
        "message": [{"type": "text", "data": {"text": f"group-{message_id}"}}],
    }


async def store(repository: SQLiteRepository, payload: dict):
    message = parse_message_event(payload, expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message)
    return message


def context_assembler(repository: SQLiteRepository) -> ProvenanceContextAssembler:
    return ProvenanceContextAssembler(
        repository,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
    )


async def configure_private_material(
    repository: SQLiteRepository,
    user_qq: str,
    marker: str,
) -> tuple[str, str]:
    persona = await PersonaService(repository).save_manual(
        scope=ProfileScope.PRIVATE_USER,
        user_qq=user_qq,
        name=f"persona-{marker}",
        definition=f"private persona {marker}",
        created_by=OWNER_QQ,
    )
    memory = await MemoryService(repository).create_manual(
        user_qq=user_qq,
        kind=MemoryKind.FACT,
        key=f"fact_{marker}",
        value={"marker": marker},
        created_by=OWNER_QQ,
    )
    return str(persona["id"]), str(memory["id"])


@pytest.mark.asyncio
async def test_concurrent_private_and_group_contexts_remain_isolated(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "combined-isolation.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    persona_a, memory_a = await configure_private_material(repository, USER_A, "A")
    persona_b, memory_b = await configure_private_material(repository, USER_B, "B")
    old_a = await store(repository, private_event(1001, USER_A, now - timedelta(minutes=5)))
    await store(repository, private_event(2001, USER_B, now - timedelta(minutes=5)))
    await repository.set_history_policy(
        user_qq=USER_A,
        mode=HistoryAccessMode.SELECTED_RANGE,
        selected_from=(now.date() - timedelta(days=1)).isoformat(),
        selected_to=(now.date() + timedelta(days=1)).isoformat(),
        max_messages=20,
        updated_by=OWNER_QQ,
        reason="combined isolation test",
    )
    await repository.set_history_policy(
        user_qq=USER_B,
        mode=HistoryAccessMode.DENY,
        updated_by=OWNER_QQ,
        reason="combined denial test",
    )
    triggers = await asyncio.gather(
        store(repository, private_event(1002, USER_A, now)),
        store(repository, private_event(2002, USER_B, now)),
        store(repository, group_event(3002, USER_A, now)),
    )
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    await asyncio.gather(
        *(orchestration.register_message(item, observed_at=now) for item in triggers)
    )
    claims = []
    for index in range(3):
        claimed = await orchestration.claim_ready(worker_id=f"worker-{index}", now=now)
        assert claimed is not None
        claims.append(claimed)

    service = context_assembler(repository)
    manifests = await asyncio.gather(
        *(
            service.assemble(
                run_id=claim["id"],
                lease_token=claim["lease"]["lease_token"],
                actor_qq=claim["subject_user_qq"] or USER_A,
                owner_qq=OWNER_QQ,
                bot_qq=BOT_QQ,
                now=now,
            )
            for claim in claims
        )
    )
    by_key = {item.conversation_key: item for item in manifests}
    private_a = by_key[f"{BOT_QQ}:private:{USER_A}"]
    private_b = by_key[f"{BOT_QQ}:private:{USER_B}"]
    group = by_key[f"{BOT_QQ}:group:{GROUP_QQ}"]

    sections_a = {item.section_id: item for item in private_a.sections}
    sections_b = {item.section_id: item for item in private_b.sections}
    assert sections_a["persona"].record_ids == (persona_a,)
    assert sections_a["memory"].record_ids == (memory_a,)
    assert sections_a["history"].record_ids == (old_a.id,)
    assert persona_b not in {value for item in private_a.sections for value in item.record_ids}
    assert memory_b not in {value for item in private_a.sections for value in item.record_ids}
    assert sections_b["persona"].record_ids == (persona_b,)
    assert sections_b["memory"].record_ids == (memory_b,)
    assert sections_b["history"].policy_decision.value == "denied"
    assert persona_a not in {value for item in private_b.sections for value in item.record_ids}
    assert memory_a not in {value for item in private_b.sections for value in item.record_ids}
    for section_id in ("knowledge", "memory", "history"):
        section = {item.section_id: item for item in group.sections}[section_id]
        assert section.policy_decision.value == "denied"
        assert section.record_ids == ()


@pytest.mark.asyncio
async def test_late_arrival_never_becomes_history_of_the_active_run(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "late-arrival.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    old = await store(repository, private_event(4001, USER_A, now - timedelta(minutes=5)))
    trigger = await store(repository, private_event(4002, USER_A, now))
    await repository.set_history_policy(
        user_qq=USER_A,
        mode=HistoryAccessMode.SELECTED_RANGE,
        selected_from=(now.date() - timedelta(days=1)).isoformat(),
        selected_to=(now.date() + timedelta(days=1)).isoformat(),
        max_messages=20,
        updated_by=OWNER_QQ,
        reason="late arrival test",
    )
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    registered = await orchestration.register_message(trigger, observed_at=now)
    claimed = await orchestration.claim_ready(worker_id="active-worker", now=now)
    assert claimed is not None

    late = await store(repository, private_event(4003, USER_A, now + timedelta(seconds=1)))
    deferred = await orchestration.register_message(late, observed_at=now + timedelta(seconds=1))
    assert deferred["deferred"] is True
    active_manifest = await context_assembler(repository).assemble(
        run_id=claimed["id"],
        lease_token=claimed["lease"]["lease_token"],
        actor_qq=USER_A,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now + timedelta(seconds=1),
    )
    history = {item.section_id: item for item in active_manifest.sections}["history"]
    assert history.record_ids == (old.id,)
    assert late.id not in history.record_ids
    assert "private-4003" not in json.loads(history.content)[0]["plain_text"]

    failure = ReplyFailure(
        code="test_handoff",
        category=ReplyFailureCategory.INTERNAL,
        retryable=False,
        safe_detail="complete active test run",
    )
    assert await repository.transition_reply_run(
        run_id=registered["run"]["id"],
        expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
        target_stage=ReplyRunStage.FAILED,
        failure=failure,
    )
    successor = await orchestration.register_message(
        late,
        observed_at=now + timedelta(seconds=2),
    )
    assert successor["created"] is False
    assert successor["run"]["id"] != registered["run"]["id"]
    successor_detail = await repository.reply_run_detail(successor["run"]["id"])
    assert [item["message_id"] for item in successor_detail["triggers"]] == [late.id]
    successor_detail = await repository.reply_run_detail(successor["run"]["id"])
    assert successor_detail is not None
    assert [item["message_id"] for item in successor_detail["triggers"]] == [late.id]
