from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _support.readiness import ALLOW_READINESS
from ych_bot.application import (
    ConversationContextService,
    FakeFirstReplyWorker,
    MemoryService,
    PersonaService,
    ProvenanceContextAssembler,
    ReplyOrchestrationService,
    ReplyOutboxService,
    ReplyPlanService,
    ReplyPlanServiceError,
)
from ych_bot.application.inbound_images import InboundImageRef
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ModelUnavailableError,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import (
    ModelCallGuard,
    ModelProtectionPolicy,
    ProtectedChatModelGateway,
)
from ych_bot.infrastructure.napcat.parser import parse_message_event

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_QQ = "123456789"
GROUP_QQ = "2000000004"


class FakeChatGateway:
    def __init__(
        self,
        outcome: ChatGenerationResult | Exception | list[ChatGenerationResult | Exception],
    ) -> None:
        self.outcome = outcome
        self._queue = list(outcome) if isinstance(outcome, list) else None
        self.requests: list[ChatGenerationRequest] = []

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.requests.append(request)
        current = self._queue.pop(0) if self._queue is not None else self.outcome
        if isinstance(current, Exception):
            raise current
        return current

    async def close(self) -> None:
        return None


SAFE_IMAGE_URL = "https://example.invalid/cat.png"


def event(
    message_id: int,
    now: datetime,
    *,
    text: str | None = "hello",
    include_face: bool = False,
    include_image: bool = False,
) -> dict:
    message: list[dict[str, object]] = []
    if include_face:
        message.append({"type": "face", "data": {"id": "0"}})
    if include_image or (text is None and not include_face):
        message.append({"type": "image", "data": {"url": SAFE_IMAGE_URL}})
    if text is not None:
        message.append({"type": "text", "data": {"text": text}})
    return {
        "time": int(now.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": int(USER_QQ),
        "message": message,
    }


def group_event(
    message_id: int,
    now: datetime,
    *,
    text: str | None = "hello",
    include_image: bool = False,
    mention_bot: bool = False,
    reply_to: str | None = None,
) -> dict:
    message: list[dict[str, object]] = []
    if reply_to is not None:
        message.append({"type": "reply", "data": {"id": reply_to}})
    if mention_bot:
        message.append({"type": "at", "data": {"qq": BOT_QQ}})
    if include_image or (text is None and not mention_bot):
        message.append({"type": "image", "data": {"url": SAFE_IMAGE_URL}})
    if text is not None:
        message.append({"type": "text", "data": {"text": text}})
    return {
        "time": int(now.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "user_id": int(USER_QQ),
        "group_id": int(GROUP_QQ),
        "message": message,
    }


async def register_ready(
    repository: SQLiteRepository,
    now: datetime,
    *,
    text: str | None = "hello",
    include_face: bool = False,
    include_image: bool = False,
) -> ReplyOrchestrationService:
    message = parse_message_event(
        event(5001, now, text=text, include_face=include_face, include_image=include_image),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(message)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    await orchestration.register_message(message, observed_at=now)
    return orchestration


def protected_gateway(
    repository: SQLiteRepository,
    provider: FakeChatGateway,
    now: datetime,
) -> ProtectedChatModelGateway:
    policy = ModelProtectionPolicy(
        route="chat",
        timezone="Asia/Shanghai",
        daily_request_limit=10,
        daily_token_limit=100_000,
        failure_threshold=3,
        cooldown_seconds=60,
        lease_seconds=60,
    )
    return ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, policy, clock=lambda: now),
        ALLOW_READINESS,
    )


def worker(
    repository: SQLiteRepository,
    orchestration: ReplyOrchestrationService,
    gateway,
    *,
    enabled: bool,
    vision_gateway=None,
    image_inliner=None,
    before_model=None,
) -> FakeFirstReplyWorker:
    assembler = ProvenanceContextAssembler(
        repository,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
    )
    return FakeFirstReplyWorker(
        repository,
        orchestration,
        assembler,
        gateway,
        enabled=enabled,
        model_route="chat-fake",
        vision_gateway=vision_gateway,
        vision_model_route="vision-fake",
        image_inliner=image_inliner,
        before_model=before_model,
    )


@pytest.mark.asyncio
async def test_fake_reply_worker_is_paused_by_default(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-paused.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now)
    provider = FakeChatGateway(ChatGenerationResult(text="unused"))

    result = await worker(repository, orchestration, provider, enabled=False).run_once(
        worker_id="paused-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "paused"
    assert provider.requests == []
    assert (await repository.counts())["inference_runs"] == 0
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_protected_fake_gateway_advances_only_to_reply_planning(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-success.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="How are you?")
    provider = FakeChatGateway(
        ChatGenerationResult(
            text="I'm fine.",
            provider_request_id="fake-provider-1",
            input_tokens=120,
            output_tokens=8,
        )
    )
    gateway = protected_gateway(repository, provider, now)

    result = await worker(repository, orchestration, gateway, enabled=True).run_once(
        worker_id="fake-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert result.stage == "planning_reply"
    assert len(provider.requests) == 1
    assert provider.requests[0].messages[-1].content == "How are you?"
    assert provider.requests[0].messages[0].role.value == "system"
    assert "不可信参考数据" in provider.requests[0].messages[0].content
    assert "core_identity" in provider.requests[0].messages[0].content
    detail = await repository.reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "planning_reply"
    assert detail["attempt_count"] == 1
    inference = await repository.inference_run(f"reply:{result.run_id}")
    assert inference is not None
    assert inference["mode"] == "reply_fake"
    assert inference["status"] == "completed"
    assert inference["candidate"]["content"] == "I'm fine."
    assert inference["candidate"]["safety_flags"] == [
        "reply_pipeline_fake",
        "not_for_delivery",
    ]
    counts = await repository.counts()
    assert counts["model_call_events"] == 1
    assert counts["outbox"] == 0

    plan = await ReplyPlanService(repository).plan(
        run_id=str(result.run_id),
        lease_token=str(detail["lease"]["lease_token"]),
        now=now,
    )
    assert [bubble.idempotency_key for bubble in plan.bubbles] == [f"{result.run_id}:bubble:1"]
    planned = await repository.reply_run_detail(result.run_id)
    assert planned is not None
    assert planned["stage"] == "creating_outbox"
    assert planned["reply_plan"]["bubbles"][0]["segments"][0]["data"]["text"] == "I'm fine."
    recovered = await ReplyPlanService(repository).plan(
        run_id=str(result.run_id),
        lease_token=str(planned["lease"]["lease_token"]),
        now=now,
    )
    assert recovered == plan
    assert (await repository.counts())["outbox"] == 0

    handed_off = await ReplyOutboxService(repository).handoff(
        run_id=str(result.run_id),
        lease_token=str(planned["lease"]["lease_token"]),
        now=now,
    )
    assert handed_off.created_count == 1
    assert handed_off.recovered is False
    awaiting = await repository.reply_run_detail(result.run_id)
    assert awaiting is not None
    assert awaiting["stage"] == "awaiting_delivery"
    assert (await repository.counts())["outbox"] == 1

    repeated = await ReplyOutboxService(repository).handoff(
        run_id=str(result.run_id),
        lease_token=str(awaiting["lease"]["lease_token"]),
        now=now,
    )
    assert repeated.outbox_ids == handed_off.outbox_ids
    assert repeated.created_count == 0
    assert repeated.recovered is True
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_model_failure_is_sanitized_and_never_creates_outbox(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-failure.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now)
    provider = FakeChatGateway(ModelUnavailableError("provider secret body"))
    gateway = protected_gateway(repository, provider, now)

    result = await worker(repository, orchestration, gateway, enabled=True).run_once(
        worker_id="failing-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "failed"
    assert result.failure_code == "model_unavailable"
    detail = await repository.reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "failed"
    assert detail["failure_category"] == "model_unavailable"
    assert detail["lease"] is None
    inference = await repository.inference_run(f"reply:{result.run_id}")
    assert inference is not None
    assert inference["status"] == "failed"
    assert "provider secret body" not in inference["error_message"]
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_model_timeout_is_classified_without_creating_outbox(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-timeout.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now)
    provider = FakeChatGateway(TimeoutError("provider timeout detail"))

    result = await worker(repository, orchestration, provider, enabled=True).run_once(
        worker_id="timeout-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "failed"
    assert result.failure_code == "model_timeout"
    detail = await repository.reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["failure_category"] == "model_timeout"
    assert "provider timeout detail" not in detail["failure_detail"]
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_empty_model_output_is_invalid_without_creating_outbox(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-invalid.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now)
    provider = FakeChatGateway(ChatGenerationResult(text="   "))

    result = await worker(repository, orchestration, provider, enabled=True).run_once(
        worker_id="invalid-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "failed"
    assert result.failure_code == "invalid_model_response"
    detail = await repository.reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["failure_category"] == "model_response"
    assert detail["reply_plan"] is None
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_unsupported_non_text_trigger_is_suppressed_without_model_call(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-unsupported.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text=None)
    provider = FakeChatGateway(ChatGenerationResult(text="must not run"))

    result = await worker(repository, orchestration, provider, enabled=True).run_once(
        worker_id="unsupported-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "suppressed"
    assert result.failure_code == "unsupported_trigger_content"
    assert provider.requests == []
    detail = await repository.reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "suppressed"
    assert detail["failure_category"] == "unsupported_input"
    counts = await repository.counts()
    assert counts["inference_runs"] == 0
    assert counts["outbox"] == 0


@pytest.mark.asyncio
async def test_face_only_trigger_uses_sanitized_placeholder(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-face.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text=" ", include_face=True)
    provider = FakeChatGateway(ChatGenerationResult(text="收到表情。"))

    result = await worker(repository, orchestration, provider, enabled=True).run_once(
        worker_id="face-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert result.stage == "planning_reply"
    assert len(provider.requests) == 1
    assert provider.requests[0].messages[-1].content == "[表情]"
    assert (await repository.counts())["inference_runs"] == 1


@pytest.mark.asyncio
async def test_image_trigger_uses_vision_gateway_not_chat(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-vision.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="看图", include_image=True)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="看见一只猫。"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="vision-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert chat.requests == []
    assert len(vision.requests) == 1
    assert vision.requests[0].messages[-1].content == "看图"
    assert vision.requests[0].messages[-1].image_urls == (SAFE_IMAGE_URL,)
    system = vision.requests[0].messages[0].content
    assert "不要当解说员" in system
    assert "这是一张" in system
    assert "说图里有什么" not in system
    inference = await repository.inference_run(f"reply:{result.run_id}")
    assert inference is not None
    assert inference["model_route"] == "vision-fake"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_text_only_skips_vision_stance_when_vision_is_configured(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-text-no-vision-stance.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="你好")
    chat = FakeChatGateway(ChatGenerationResult(text="在呢。"))
    vision = FakeChatGateway(ChatGenerationResult(text="must not run"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="text-no-vision-stance",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert vision.requests == []
    assert len(chat.requests) == 1
    system = chat.requests[0].messages[0].content
    assert "不要当解说员" not in system
    assert "说图里有什么" not in system


@pytest.mark.asyncio
async def test_vision_inliner_replaces_http_url_before_model_call(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-vision-inline.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="看图", include_image=True)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="看见色块。"))
    data_uri = "data:image/png;base64,ZmFrZQ=="

    async def inliner(images: tuple[object, ...]) -> tuple[str, ...]:
        assert images == (InboundImageRef(url=SAFE_IMAGE_URL),)
        return (data_uri,)

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
        image_inliner=inliner,
    ).run_once(
        worker_id="vision-inline-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert chat.requests == []
    assert vision.requests[0].messages[-1].image_urls == (data_uri,)
    assert vision.requests[0].max_output_tokens == 80


@pytest.mark.asyncio
async def test_group_mention_attaches_same_sender_standalone_image(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-group-image.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    earlier = now - timedelta(seconds=20)
    image = parse_message_event(
        group_event(6001, earlier, text=None, include_image=True),
        expected_bot_qq=BOT_QQ,
    )
    mention = parse_message_event(
        group_event(6002, now, text="这是什么", mention_bot=True),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(image)
    assert await repository.store_inbound(mention)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    await orchestration.register_message(mention, observed_at=now)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="看见色块。"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="group-image-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert chat.requests == []
    assert vision.requests[0].messages[-1].image_urls == (SAFE_IMAGE_URL,)
    assert vision.requests[0].messages[-1].content == "这是什么"


@pytest.mark.asyncio
async def test_group_at_only_attaches_same_sender_standalone_image(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-group-at-only.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    earlier = now - timedelta(seconds=20)
    image = parse_message_event(
        group_event(6101, earlier, text=None, include_image=True),
        expected_bot_qq=BOT_QQ,
    )
    mention = parse_message_event(
        group_event(6102, now, text=None, mention_bot=True),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(image)
    assert await repository.store_inbound(mention)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    await orchestration.register_message(mention, observed_at=now)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="看见色块。"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="group-at-only-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert chat.requests == []
    assert vision.requests[0].messages[-1].content == "（刚甩过来一张图）"
    assert vision.requests[0].messages[-1].image_urls == (SAFE_IMAGE_URL,)


@pytest.mark.asyncio
async def test_inliner_does_not_run_when_model_gate_blocks(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-inline-blocked.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="看图", include_image=True)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="must not run"))
    fetches: list[tuple[str, ...]] = []

    async def inliner(urls: tuple[str, ...]) -> tuple[str, ...]:
        fetches.append(urls)
        return urls

    async def block(_run_id: str, _lease_token: str) -> bool:
        return False

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
        image_inliner=inliner,
        before_model=block,
    ).run_once(
        worker_id="inline-blocked-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "blocked"
    assert fetches == []
    assert chat.requests == []
    assert vision.requests == []


@pytest.mark.asyncio
async def test_image_only_uses_vision_placeholder_when_configured(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-vision-only.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text=None)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="看见一只猫。"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="vision-only-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert chat.requests == []
    assert vision.requests[0].messages[-1].content == "（刚甩过来一张图）"
    assert vision.requests[0].messages[-1].image_urls == (SAFE_IMAGE_URL,)


@pytest.mark.asyncio
async def test_vision_retries_once_when_first_reply_is_a_caption(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-vision-caption-retry.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="看图", include_image=True)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(
        [
            ChatGenerationResult(text="这是一张测试图。"),
            ChatGenerationResult(text="这哪拍的啊。"),
        ]
    )

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="vision-caption-retry",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert len(vision.requests) == 2
    assert "刚才那种介绍图的说法不行" in vision.requests[1].messages[-1].content
    inference = await repository.inference_run(f"reply:{result.run_id}")
    assert inference is not None
    assert inference["candidate"]["content"] == "这哪拍的啊。"


@pytest.mark.asyncio
async def test_vision_keeps_explain_reply_when_user_asked(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-worker-vision-explain-ask.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now, text="这是什么", include_image=True)
    chat = FakeChatGateway(ChatGenerationResult(text="must not run"))
    vision = FakeChatGateway(ChatGenerationResult(text="这是一张电路板。"))

    result = await worker(
        repository,
        orchestration,
        chat,
        enabled=True,
        vision_gateway=vision,
    ).run_once(
        worker_id="vision-explain-ask",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )

    assert result.status == "generated"
    assert len(vision.requests) == 1
    inference = await repository.inference_run(f"reply:{result.run_id}")
    assert inference is not None
    assert inference["candidate"]["content"] == "这是一张电路板。"


@pytest.mark.asyncio
async def test_oversized_candidate_fails_planning_without_partial_plan(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-plan-invalid.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    orchestration = await register_ready(repository, now)
    provider = FakeChatGateway(ChatGenerationResult(text="长" * 81))
    generated = await worker(repository, orchestration, provider, enabled=True).run_once(
        worker_id="planning-worker",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        now=now,
    )
    detail = await repository.reply_run_detail(generated.run_id)
    assert detail is not None

    with pytest.raises(ReplyPlanServiceError, match="capacity"):
        await ReplyPlanService(repository).plan(
            run_id=str(generated.run_id),
            lease_token=str(detail["lease"]["lease_token"]),
            now=now,
        )

    failed = await repository.reply_run_detail(generated.run_id)
    assert failed is not None
    assert failed["stage"] == "failed"
    assert failed["failure_code"] == "invalid_reply_candidate"
    assert failed["failure_category"] == "model_response"
    assert failed["reply_plan"] is None
    assert (await repository.counts())["outbox"] == 0
