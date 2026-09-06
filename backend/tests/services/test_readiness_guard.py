from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ych_bot.application import (
    AccountService,
    ReadinessBlockedError,
    ReadinessGuard,
    ReadinessService,
)
from ych_bot.domain.control import ReportSeverity
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ImageGenerationRequest,
    ImageGenerationResult,
    ModelBudgetExceededError,
    ModelMessage,
    ModelRole,
)
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    ProbeStatus,
    ReadinessProbeResult,
    ReadinessProfile,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import (
    ModelCallGuard,
    ModelProtectionPolicy,
    ProtectedChatModelGateway,
    ProtectedImageModelGateway,
)
from ych_bot.workers.outbox import OutboxDispatcher

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current


class SyntheticProbe:
    def __init__(self, code: str, state: dict[tuple[str, CapabilityScope], ProbeStatus]) -> None:
        self.code = code
        self._state = state
        self.fixed_expiry: datetime | None = None

    async def collect(self, context) -> ReadinessProbeResult:
        status = self._state.get((self.code, context.capability_scope), ProbeStatus.PASS)
        observed = NOW if self.fixed_expiry else context.now
        expires = self.fixed_expiry or context.now + timedelta(seconds=30)
        return ReadinessProbeResult(
            probe_code=self.code,
            status=status,
            capability_scope=context.capability_scope,
            freshness=EvidenceFreshness(observed_at=observed, expires_at=expires),
            source="synthetic-boundary-test",
            source_revision=f"{self.code}:{status.value}",
            safe_detail=f"synthetic {status.value}",
        )


async def guarded_runtime(
    repository: SQLiteRepository,
    clock: MutableClock,
    *,
    state: dict[tuple[str, CapabilityScope], ProbeStatus] | None = None,
    fixed_expiry: datetime | None = None,
) -> tuple[ReadinessGuard, dict[str, SyntheticProbe]]:
    await AccountService(repository).ensure_seeded(owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    codes = ReadinessService.required_probe_codes(
        ReadinessProfile.CONTROLLED_REAL_EFFECT,
        CapabilityScope.QQ_REPLY,
    )
    probes = {code: SyntheticProbe(code, state or {}) for code in codes}
    if fixed_expiry is not None:
        for probe in probes.values():
            probe.fixed_expiry = fixed_expiry
    service = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="boundary-process",
        probes=probes,
        clock=clock,
    )
    guard = ReadinessGuard(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="boundary-process",
        clock=clock,
    )
    guard.bind(service)
    return guard, probes


def chat_policy(*, request_limit: int = 10) -> ModelProtectionPolicy:
    return ModelProtectionPolicy(
        route="chat",
        timezone="Asia/Shanghai",
        daily_request_limit=request_limit,
        daily_token_limit=100_000,
        failure_threshold=3,
        cooldown_seconds=60,
        lease_seconds=60,
    )


class ChatProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.calls += 1
        return ChatGenerationResult(text=f"ok:{request.request_id}")

    async def close(self) -> None:
        return None


class ImageProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.calls += 1
        return ImageGenerationResult(artifacts=("synthetic-image",))

    async def close(self) -> None:
        return None


class DeliveryClient:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def send_message(self, message: OutboundMessage) -> dict[str, Any]:
        self.messages.append(message)
        return {"status": "ok"}


def chat_request(request_id: str) -> ChatGenerationRequest:
    return ChatGenerationRequest(
        messages=(ModelMessage(ModelRole.USER, "synthetic"),),
        request_id=request_id,
    )


@pytest.mark.asyncio
async def test_capability_specific_readiness_blocks_image_but_not_chat(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "capability.sqlite3")
    await repository.initialize()
    clock = MutableClock()
    guard, _ = await guarded_runtime(
        repository,
        clock,
        state={
            ("capability.gate", CapabilityScope.IMAGE_MODEL): ProbeStatus.BLOCKED,
        },
    )
    chat_provider = ChatProvider()
    image_provider = ImageProvider()
    chat = ProtectedChatModelGateway(
        chat_provider,
        ModelCallGuard(repository, chat_policy(), clock=clock),
        guard,
    )
    image_call_guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="image",
            timezone="Asia/Shanghai",
            daily_request_limit=10,
            daily_token_limit=None,
            failure_threshold=3,
            cooldown_seconds=60,
            lease_seconds=60,
        ),
        clock=clock,
    )
    image = ProtectedImageModelGateway(image_provider, image_call_guard, guard)

    assert (await chat.generate(chat_request("chat-pass"))).text == "ok:chat-pass"
    with pytest.raises(ReadinessBlockedError):
        await image.generate(ImageGenerationRequest(prompt="blocked", request_id="image-block"))

    assert chat_provider.calls == 1
    assert image_provider.calls == 0
    assert (await image_call_guard.snapshot()).requests_used == 0
    with sqlite3.connect(repository.path) as connection:
        actions = connection.execute(
            "SELECT action FROM audit_log WHERE action = 'readiness.external_effect_blocked'"
        ).fetchall()
        reports = connection.execute(
            """
            SELECT delivery_policy, delivery_status FROM owner_report_runtime
            JOIN owner_reports ON owner_reports.id = owner_report_runtime.report_id
            WHERE owner_reports.category = 'operational_readiness'
            """
        ).fetchall()
    assert actions
    assert reports and all(row == ("dashboard_only", "suppressed") for row in reports)


@pytest.mark.asyncio
async def test_readiness_pass_never_bypasses_independent_model_budget(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "budget.sqlite3")
    await repository.initialize()
    clock = MutableClock()
    guard, _ = await guarded_runtime(repository, clock)
    provider = ChatProvider()
    gateway = ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, chat_policy(request_limit=1), clock=clock),
        guard,
    )

    await gateway.generate(chat_request("first"))
    with pytest.raises(ModelBudgetExceededError):
        await gateway.generate(chat_request("second"))

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_absolute_expiry_blocks_a_later_boundary_call(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "stale.sqlite3")
    await repository.initialize()
    clock = MutableClock()
    guard, _ = await guarded_runtime(
        repository,
        clock,
        fixed_expiry=NOW + timedelta(seconds=1),
    )
    provider = ChatProvider()
    gateway = ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, chat_policy(), clock=clock),
        guard,
    )

    await gateway.generate(chat_request("fresh"))
    clock.current += timedelta(seconds=2)
    with pytest.raises(ReadinessBlockedError, match="evidence_stale"):
        await gateway.generate(chat_request("stale"))

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_capability_state_change_after_a_pass_fails_closed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "state-change.sqlite3")
    await repository.initialize()
    clock = MutableClock()
    state = {("capability.gate", CapabilityScope.CHAT_MODEL): ProbeStatus.PASS}
    guard, _ = await guarded_runtime(repository, clock, state=state)
    provider = ChatProvider()
    gateway = ProtectedChatModelGateway(
        provider,
        ModelCallGuard(repository, chat_policy(), clock=clock),
        guard,
    )

    await gateway.generate(chat_request("state-pass"))
    state[("capability.gate", CapabilityScope.CHAT_MODEL)] = ProbeStatus.BLOCKED
    with pytest.raises(ReadinessBlockedError):
        await gateway.generate(chat_request("state-blocked"))

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_blocked_outbox_is_terminal_without_network_or_automatic_retry(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "outbox.sqlite3")
    await repository.initialize()
    clock = MutableClock()
    guard, _ = await guarded_runtime(
        repository,
        clock,
        state={
            ("capability.gate", CapabilityScope.QQ_REPLY): ProbeStatus.BLOCKED,
        },
    )
    outbound = OutboundMessage(
        id="blocked-outbound",
        idempotency_key="blocked-outbound",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="123456789",
        segments=(MessageSegment("text", {"text": "never sent"}),),
        created_at=NOW,
    )
    assert await repository.enqueue_outbound(outbound)
    client = DeliveryClient()
    dispatcher = OutboxDispatcher(
        repository,
        client,  # type: ignore[arg-type]
        enabled=True,
        readiness_guard=guard,
    )

    assert await dispatcher.run_once() == 0
    assert await dispatcher.run_once() == 0
    assert client.messages == []
    with sqlite3.connect(repository.path) as connection:
        row = connection.execute(
            "SELECT status, last_error FROM outbox WHERE id = 'blocked-outbound'"
        ).fetchone()
    assert row is not None and row[0] == "rejected"
    assert str(row[1]).startswith("readiness_")


@pytest.mark.asyncio
async def test_outbox_resolves_proactive_and_owner_report_effect_scopes(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "outbox-scopes.sqlite3")
    await repository.initialize()
    proactive_id = await repository.enqueue_auto_proactive_message(
        target_qq="123456789",
        content="synthetic proactive",
        content_source="test",
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        now=NOW,
    )
    proactive = await repository.proactive_task(proactive_id)
    assert proactive is not None
    assert (
        await repository.outbound_effect_scope(str(proactive["outbox_id"]))
        is CapabilityScope.QQ_PROACTIVE
    )

    report = await repository.record_owner_report(
        severity=ReportSeverity.WARNING,
        category="synthetic",
        title="boundary scope",
        body="synthetic owner report",
        now=NOW,
    )
    outbox_id = await repository.enqueue_owner_report_outbox(
        report_id=report["id"],
        owner_qq=OWNER_QQ,
        title="boundary scope",
        body="synthetic owner report",
        occurrence_count=1,
        now=NOW,
    )
    assert outbox_id is not None
    assert await repository.outbound_effect_scope(outbox_id) is CapabilityScope.OWNER_REPORT
    assert await repository.mark_owner_report_queued(
        report["id"],
        outbox_id=outbox_id,
        now=NOW,
    )
    assert await repository.outbound_effect_scope(outbox_id) is CapabilityScope.OWNER_REPORT
