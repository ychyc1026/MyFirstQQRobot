from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _support.paths import FIXTURES_ROOT
from _support.readiness import ALLOW_READINESS
from ych_bot.application import (
    ConversationContextService,
    FakeFirstReplyWorker,
    MemoryService,
    PersonaService,
    ProvenanceContextAssembler,
    ReplyOperationsService,
    ReplyOrchestrationService,
    ReplyOutboxService,
    ReplyPipelineFlags,
    ReplyPlanService,
    ReplyStyleService,
)
from ych_bot.domain.errors import NapCatDeliveryUnknownError
from ych_bot.domain.modeling import ChatGenerationRequest, ChatGenerationResult
from ych_bot.domain.models import OutboundMessage
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event
from ych_bot.workers.outbox import OutboxDispatcher

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_QQ = "123456789"
FIXTURES = FIXTURES_ROOT / "onebot"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[ChatGenerationRequest] = []

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.requests.append(request)
        return ChatGenerationResult(
            text=self.text,
            provider_request_id="synthetic-model-request",
            input_tokens=80,
            output_tokens=12,
        )

    async def close(self) -> None:
        return None


class FakeTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[OutboundMessage] = []

    async def send_message(self, message: OutboundMessage) -> dict:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return {"status": "ok", "retcode": 0, "data": {"message_id": len(self.messages)}}


def assembler(repository: SQLiteRepository) -> ProvenanceContextAssembler:
    return ProvenanceContextAssembler(
        repository,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
    )


def operations(repository: SQLiteRepository) -> ReplyOperationsService:
    return ReplyOperationsService(
        repository,
        flags=ReplyPipelineFlags(
            ingestion_enabled=True,
            chat_model_configured=True,
            model_network_enabled=False,
            chat_route_enabled=False,
            outbound_enabled=False,
            onebot_token_configured=False,
        ),
    )


async def register_fixture(
    repository: SQLiteRepository,
    fixture_name: str,
    *,
    settle_seconds: float,
) -> tuple[ReplyOrchestrationService, datetime]:
    payload = fixture(fixture_name)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=settle_seconds)
    observed_base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    latest = observed_base
    for index, event in enumerate(payload["events"]):
        message = parse_message_event(event, expected_bot_qq=BOT_QQ)
        assert await repository.store_inbound(message)
        observed_at = observed_base + timedelta(seconds=index)
        await orchestration.register_message(
            message,
            observed_at=observed_at,
            policy_snapshot={"mode": "synthetic_e2e"},
        )
        latest = observed_at
    return orchestration, latest + timedelta(seconds=settle_seconds + 1)


@pytest.mark.asyncio
async def test_private_burst_runs_from_onebot_fixture_to_confirmed_delivery(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-e2e-success.sqlite3")
    await repository.initialize()
    orchestration, ready_at = await register_fixture(
        repository,
        "private_text_burst.json",
        settle_seconds=2,
    )
    await ReplyStyleService(repository).update_policy(
        user_qq=USER_QQ,
        min_bubbles=2,
        max_bubbles=2,
        sentence_min_chars=4,
        sentence_max_chars=40,
        updated_by=OWNER_QQ,
    )
    model = FakeModel("听起来是很好的时刻。谢谢你愿意告诉我。")
    worker = FakeFirstReplyWorker(
        repository,
        orchestration,
        assembler(repository),
        model,
        enabled=True,
        model_route="synthetic-chat",
    )

    generated = await worker.run_once(
        worker_id="synthetic-reply-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=ready_at,
    )
    failed_detail = (
        await repository.reply_run_detail(str(generated.run_id)) if generated.run_id else None
    )
    assert generated.status == "generated", (
        failed_detail.get("failure_code") if failed_detail else None,
        failed_detail.get("failure_detail") if failed_detail else None,
    )
    assert generated.run_id is not None
    run_id = generated.run_id
    detail = await repository.reply_run_detail(run_id)
    assert detail is not None
    assert len(detail["triggers"]) == 2
    assert [item["plain_text"] for item in detail["triggers"]] == [
        "今天有点开心",
        "想和你说一声",
    ]
    assert len(model.requests) == 1
    assert "今天有点开心" in model.requests[0].messages[-1].content
    assert "想和你说一声" in model.requests[0].messages[-1].content

    lease_token = str(detail["lease"]["lease_token"])
    plan = await ReplyPlanService(repository).plan(
        run_id=run_id,
        lease_token=lease_token,
        now=ready_at,
    )
    assert len(plan.bubbles) == 2
    handoff = await ReplyOutboxService(repository).handoff(
        run_id=run_id,
        lease_token=lease_token,
        now=ready_at,
    )
    assert handoff.created_count == 2

    transport = FakeTransport()
    dispatcher = OutboxDispatcher(
        repository,
        transport,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=ALLOW_READINESS,
        default_bot_qq=BOT_QQ,
    )
    assert await dispatcher.run_once() == 1
    assert await dispatcher.run_once() == 1
    assert await dispatcher.run_once() == 0
    assert len(transport.messages) == 2
    finished = await operations(repository).detail(run_id)
    assert finished is not None
    assert finished["stage"] == "completed"
    assert [item["outcome"] for item in finished["delivery"]].count("delivered") == 2


@pytest.mark.asyncio
async def test_group_fixture_preserves_segments_and_ambiguous_send_is_terminal(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-e2e-unknown.sqlite3")
    await repository.initialize()
    orchestration, ready_at = await register_fixture(
        repository,
        "group_mixed_segments.json",
        settle_seconds=0,
    )
    model = FakeModel("今晚可以一起聊点轻松的话题。")
    worker = FakeFirstReplyWorker(
        repository,
        orchestration,
        assembler(repository),
        model,
        enabled=True,
        model_route="synthetic-chat",
    )
    generated = await worker.run_once(
        worker_id="synthetic-group-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=ready_at,
    )
    assert generated.run_id is not None
    run_id = generated.run_id
    detail = await repository.reply_run_detail(run_id)
    assert detail is not None
    assert generated.status == "generated", (
        detail.get("failure_code"),
        detail.get("failure_detail"),
    )
    assert [item["type"] for item in detail["triggers"][0]["segments"]] == [
        "at",
        "text",
        "image",
    ]
    assert all(
        section["scope"] != "user" or section["policy_decision"] == "denied"
        for section in detail["context_manifests"][0]["manifest"]["sections"]
    )
    lease_token = str(detail["lease"]["lease_token"])
    await ReplyPlanService(repository).plan(
        run_id=run_id,
        lease_token=lease_token,
        now=ready_at,
    )
    await ReplyOutboxService(repository).handoff(
        run_id=run_id,
        lease_token=lease_token,
        now=ready_at,
    )
    transport = FakeTransport(NapCatDeliveryUnknownError("synthetic timeout"))
    dispatcher = OutboxDispatcher(
        repository,
        transport,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=ALLOW_READINESS,
    )

    assert await dispatcher.run_once() == 0
    assert await dispatcher.run_once() == 0
    assert len(transport.messages) == 1
    failed = await operations(repository).detail(run_id)
    assert failed is not None
    assert failed["stage"] == "failed"
    assert failed["failure"]["category"] == "delivery_unknown"
