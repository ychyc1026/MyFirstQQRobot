from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from _support.readiness import ALLOW_READINESS
from ych_bot.application import ReplyOrchestrationService
from ych_bot.domain.errors import NapCatDeliveryUnknownError, NapCatRequestError
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.domain.reply_pipeline import ReplyBubble, ReplyPlan, ReplyRunStage
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event
from ych_bot.workers.outbox import OutboxDispatcher

BOT_QQ = "2000000002"
USER_QQ = "123456789"


class FakeDeliveryClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[OutboundMessage] = []

    async def send_message(self, message: OutboundMessage) -> dict:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return {"status": "ok", "data": {"message_id": len(self.messages)}}


def event(message_id: int, now: datetime) -> dict:
    return {
        "time": int(now.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": int(USER_QQ),
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }


async def prepare_two_bubble_plan(
    repository: SQLiteRepository,
    now: datetime,
    *,
    message_id: int,
) -> tuple[str, str, ReplyPlan]:
    message = parse_message_event(event(message_id, now), expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    registered = await orchestration.register_message(message, observed_at=now)
    claimed = await orchestration.claim_ready(worker_id="outbox-test", now=now)
    assert claimed is not None
    run_id = str(registered["run"]["id"])
    assert claimed["id"] == run_id
    lease_token = str(claimed["lease"]["lease_token"])
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
        target_stage=ReplyRunStage.CALLING_MODEL,
        lease_token=lease_token,
        now=now,
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.CALLING_MODEL,
        target_stage=ReplyRunStage.PLANNING_REPLY,
        lease_token=lease_token,
        now=now,
    )
    plan = ReplyPlan(
        run_id=run_id,
        bubbles=tuple(
            ReplyBubble(
                sequence=sequence,
                idempotency_key=f"{run_id}:bubble:{sequence}",
                segments=(MessageSegment("text", {"text": text}),),
            )
            for sequence, text in ((1, "first"), (2, "second"))
        ),
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.PLANNING_REPLY,
        target_stage=ReplyRunStage.CREATING_OUTBOX,
        reply_plan=plan,
        lease_token=lease_token,
        now=now,
    )
    return run_id, lease_token, plan


@pytest.mark.asyncio
async def test_reply_plan_handoff_is_ordered_and_idempotent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-outbox-idempotent.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, plan = await prepare_two_bubble_plan(repository, now, message_id=6001)

    first = await repository.handoff_reply_plan_to_outbox(
        run_id=run_id, lease_token=lease_token, now=now
    )
    assert first["created_count"] == 2
    assert first["recovered"] is False
    assert len(first["outbox_ids"]) == 2
    detail = await repository.reply_run_detail(run_id)
    assert detail is not None
    assert detail["stage"] == ReplyRunStage.AWAITING_DELIVERY.value

    repeated = await repository.handoff_reply_plan_to_outbox(
        run_id=run_id, lease_token=lease_token, now=now
    )
    assert repeated["outbox_ids"] == first["outbox_ids"]
    assert repeated["created_count"] == 0
    assert repeated["recovered"] is True
    assert (await repository.counts())["outbox"] == 2

    claimed_first = await repository.claim_outbound()
    claimed_second = await repository.claim_outbound()
    assert claimed_first is not None
    assert claimed_first.idempotency_key == plan.bubbles[0].idempotency_key
    assert claimed_second is None
    await repository.mark_outbound_sent(claimed_first.id)
    claimed_second = await repository.claim_outbound()
    assert claimed_second is not None
    assert claimed_second.idempotency_key == plan.bubbles[1].idempotency_key


@pytest.mark.asyncio
async def test_reply_plan_handoff_rolls_back_all_new_items_on_conflict(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-outbox-conflict.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, plan = await prepare_two_bubble_plan(repository, now, message_id=6002)
    conflict = OutboundMessage(
        id=str(uuid4()),
        idempotency_key=plan.bubbles[1].idempotency_key,
        conversation_kind=ConversationKind.PRIVATE,
        target_id="different-target",
        segments=(MessageSegment("text", {"text": "conflict"}),),
        created_at=now,
    )
    assert await repository.enqueue_outbound(conflict)

    with pytest.raises(ValueError, match="payload conflict"):
        await repository.handoff_reply_plan_to_outbox(
            run_id=run_id,
            lease_token=lease_token,
            now=now,
        )

    assert (await repository.counts())["outbox"] == 1
    detail = await repository.reply_run_detail(run_id)
    assert detail is not None
    assert detail["stage"] == ReplyRunStage.CREATING_OUTBOX.value


@pytest.mark.asyncio
async def test_reply_dispatcher_completes_ordered_bubbles_once(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-delivery-success.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, _ = await prepare_two_bubble_plan(repository, now, message_id=6003)
    await repository.handoff_reply_plan_to_outbox(run_id=run_id, lease_token=lease_token, now=now)
    client = FakeDeliveryClient()
    dispatcher = OutboxDispatcher(
        repository,
        client,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=ALLOW_READINESS,
        default_bot_qq=BOT_QQ,
    )

    assert await dispatcher.run_once() == 1
    halfway = await repository.reply_run_detail(run_id)
    assert halfway is not None
    assert halfway["stage"] == ReplyRunStage.AWAITING_DELIVERY.value
    assert [item["outcome"] for item in halfway["delivery_evidence"]] == [
        "queued",
        "sending",
        "delivered",
        "queued",
    ]

    assert await dispatcher.run_once() == 1
    completed = await repository.reply_run_detail(run_id)
    assert completed is not None
    assert completed["stage"] == ReplyRunStage.COMPLETED.value
    assert completed["lease"] is None
    assert await dispatcher.run_once() == 0
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_confirmed_reply_rejection_is_terminal_without_retry(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-delivery-rejected.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, _ = await prepare_two_bubble_plan(repository, now, message_id=6004)
    await repository.handoff_reply_plan_to_outbox(run_id=run_id, lease_token=lease_token, now=now)
    client = FakeDeliveryClient(NapCatRequestError("unsafe provider detail"))
    dispatcher = OutboxDispatcher(
        repository,
        client,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=ALLOW_READINESS,
    )

    assert await dispatcher.run_once() == 0
    failed = await repository.reply_run_detail(run_id)
    assert failed is not None
    assert failed["stage"] == ReplyRunStage.FAILED.value
    assert failed["failure_code"] == "onebot_send_rejected"
    assert failed["failure_category"] == "delivery_rejected"
    assert "unsafe provider detail" not in failed["failure_detail"]
    assert "rejected" in [item["outcome"] for item in failed["delivery_evidence"]]
    assert await dispatcher.run_once() == 0
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_ambiguous_reply_delivery_is_quarantined_without_retry(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-delivery-unknown.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, _ = await prepare_two_bubble_plan(repository, now, message_id=6005)
    await repository.handoff_reply_plan_to_outbox(run_id=run_id, lease_token=lease_token, now=now)
    client = FakeDeliveryClient(NapCatDeliveryUnknownError("socket secret"))
    dispatcher = OutboxDispatcher(
        repository,
        client,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=ALLOW_READINESS,
    )

    assert await dispatcher.run_once() == 0
    failed = await repository.reply_run_detail(run_id)
    assert failed is not None
    assert failed["stage"] == ReplyRunStage.FAILED.value
    assert failed["failure_code"] == "onebot_delivery_unknown"
    assert failed["failure_category"] == "delivery_unknown"
    assert "socket secret" not in failed["failure_detail"]
    assert "delivery_unknown" in [item["outcome"] for item in failed["delivery_evidence"]]
    assert await dispatcher.run_once() == 0
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_interrupted_reply_send_is_quarantined_during_recovery(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-delivery-recovery.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token, _ = await prepare_two_bubble_plan(repository, now, message_id=6006)
    await repository.handoff_reply_plan_to_outbox(run_id=run_id, lease_token=lease_token, now=now)
    assert await repository.claim_outbound() is not None

    assert await repository.recover_sending_outbound() == 1
    failed = await repository.reply_run_detail(run_id)
    assert failed is not None
    assert failed["stage"] == ReplyRunStage.FAILED.value
    assert failed["failure_category"] == "delivery_unknown"
    assert "delivery_unknown" in [item["outcome"] for item in failed["delivery_evidence"]]
    assert await repository.claim_outbound() is None
