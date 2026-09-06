from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _support.readiness import ALLOW_READINESS
from ych_bot.application import ReplyOrchestrationService
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.domain.reply_pipeline import ReplyRunStage
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event
from ych_bot.workers.outbox import OutboxDispatcher

BOT_QQ = "2000000002"
USER_QQ = "123456789"


class FakeDeliveryClient:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def send_message(self, message: OutboundMessage) -> dict:
        self.messages.append(message)
        return {"status": "ok", "data": {"message_id": len(self.messages)}}


def event(message_id: int, *, user_id: int = 123456789) -> dict:
    return {
        "time": 1_700_000_000 + message_id,
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": f"message-{message_id}"}}],
    }


async def store(repository: SQLiteRepository, message_id: int, *, user_id: int = 123456789):
    message = parse_message_event(event(message_id, user_id=user_id), expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message)
    return message


@pytest.mark.asyncio
async def test_expired_context_lease_is_recovered_after_restart_or_sleep(tmp_path: Path) -> None:
    database_path = tmp_path / "reply-restart.sqlite3"
    first_repository = SQLiteRepository(database_path)
    await first_repository.initialize()
    service = ReplyOrchestrationService(first_repository, settle_seconds=0)
    base = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
    message = await store(first_repository, 7101)
    registered = await service.register_message(message, observed_at=base)
    first_claim = await service.claim_ready(
        worker_id="worker-before-sleep", lease_ttl_seconds=10, now=base
    )
    assert first_claim is not None
    assert first_claim["recovered"] is False

    restarted_repository = SQLiteRepository(database_path)
    await restarted_repository.initialize()
    restarted_service = ReplyOrchestrationService(restarted_repository, settle_seconds=0)
    assert (
        await restarted_service.claim_ready(
            worker_id="worker-too-early",
            lease_ttl_seconds=10,
            now=base + timedelta(seconds=9),
        )
        is None
    )

    recovered = await restarted_service.claim_ready(
        worker_id="worker-after-wake",
        lease_ttl_seconds=10,
        now=base + timedelta(seconds=11),
    )
    assert recovered is not None
    assert recovered["id"] == registered["run"]["id"]
    assert recovered["stage"] == ReplyRunStage.ASSEMBLING_CONTEXT.value
    assert recovered["recovered"] is True
    assert recovered["lease"]["lease_token"] != first_claim["lease"]["lease_token"]
    assert not await restarted_repository.transition_reply_run(
        run_id=str(recovered["id"]),
        expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
        target_stage=ReplyRunStage.CALLING_MODEL,
        lease_token=str(first_claim["lease"]["lease_token"]),
        now=base + timedelta(seconds=11),
    )
    detail = await restarted_repository.reply_run_detail(str(recovered["id"]))
    assert detail is not None
    assert detail["stage"] == ReplyRunStage.ASSEMBLING_CONTEXT.value
    assert (await restarted_repository.list_reply_runs())["total"] == 1


@pytest.mark.asyncio
async def test_replayed_event_and_concurrent_workers_do_not_duplicate_work(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-replay.sqlite3")
    await repository.initialize()
    message = parse_message_event(event(7201), expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message) is True
    assert await repository.store_inbound(message) is False
    service = ReplyOrchestrationService(repository, settle_seconds=0)
    base = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)

    first_registration = await service.register_message(message, observed_at=base)
    replay_registration = await service.register_message(message, observed_at=base)
    assert first_registration["created"] is True
    assert replay_registration["created"] is False
    assert replay_registration["trigger_added"] is False

    claims = await asyncio.gather(
        service.claim_ready(worker_id="worker-a", now=base),
        service.claim_ready(worker_id="worker-b", now=base),
    )
    assert sum(claim is not None for claim in claims) == 1
    runs = await repository.list_reply_runs()
    assert runs["total"] == 1
    detail = await repository.reply_run_detail(str(runs["items"][0]["id"]))
    assert detail is not None
    assert len(detail["triggers"]) == 1


@pytest.mark.asyncio
async def test_durable_emergency_pause_blocks_outbox_across_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "reply-pause.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    outbound = OutboundMessage(
        id="pause-outbound",
        idempotency_key="pause-outbound",
        conversation_kind=ConversationKind.PRIVATE,
        target_id=USER_QQ,
        segments=(MessageSegment("text", {"text": "synthetic only"}),),
        created_at=now,
    )
    assert await repository.enqueue_outbound(outbound)
    first_client = FakeDeliveryClient()
    first = OutboxDispatcher(
        repository,
        first_client,
        enabled=True,
        readiness_guard=ALLOW_READINESS,
        default_bot_qq=BOT_QQ,
    )
    assert first.pause() is True
    assert await first.run_once() == 0
    assert first_client.messages == []

    restarted_repository = SQLiteRepository(database_path)
    await restarted_repository.initialize()
    restarted_client = FakeDeliveryClient()
    restarted = OutboxDispatcher(
        restarted_repository,
        restarted_client,
        enabled=True,
        readiness_guard=ALLOW_READINESS,
        default_bot_qq=BOT_QQ,
    )
    assert restarted.snapshot()["paused"] is True
    assert await restarted.run_once() == 0
    assert restarted_client.messages == []
    assert restarted.resume() is True
    assert await restarted.run_once() == 1
    assert len(restarted_client.messages) == 1
