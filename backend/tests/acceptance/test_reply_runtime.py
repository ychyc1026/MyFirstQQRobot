from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api import create_app
from ych_bot.application import (
    ConversationContextService,
    FakeFirstReplyWorker,
    MemoryService,
    MessageIngestionService,
    PersonaService,
    ProductionReplyRuntimeService,
    ProvenanceContextAssembler,
    ReplyOrchestrationService,
    ReplyOutboxService,
    ReplyPlanService,
    ReplyRuntimeControlError,
    ReplyRuntimeControlService,
    ReplyRuntimeGates,
)
from ych_bot.application.reply_runtime import is_production_auto_conversation
from ych_bot.config import Settings
from ych_bot.domain.modeling import ChatGenerationRequest, ChatGenerationResult
from ych_bot.domain.reply_pipeline import ReplyActivationMode
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event
from ych_bot.workers import ReplyRuntimeWorker

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_QQ = "123456789"
AUTO_TEST_QQ = "2000000003"
AUTO_TEST_GROUP = "2000000004"


def test_production_auto_conversation_requires_exact_bot() -> None:
    assert is_production_auto_conversation(f"{BOT_QQ}:private:{USER_QQ}", BOT_QQ)
    assert is_production_auto_conversation(f"{BOT_QQ}:group:{AUTO_TEST_GROUP}", BOT_QQ)
    assert not is_production_auto_conversation(f"9999999999:private:{USER_QQ}", BOT_QQ)
    assert not is_production_auto_conversation(f"{BOT_QQ}:channel:{USER_QQ}", BOT_QQ)
    assert not is_production_auto_conversation(f"{BOT_QQ}:private:", BOT_QQ)


def onebot_event(
    message_id: int,
    now: datetime,
    user_qq: str = USER_QQ,
    *,
    group_id: str | None = None,
    mention_bot: bool = False,
    text: str | None = None,
    include_face: bool = False,
) -> dict:
    message: list[dict[str, object]] = []
    if mention_bot:
        message.append({"type": "at", "data": {"qq": BOT_QQ}})
    if include_face:
        message.append({"type": "face", "data": {"id": "0"}})
    body = (
        " "
        if include_face and text is None
        else (f"synthetic-{message_id}" if text is None else text)
    )
    if body is not None:
        message.append({"type": "text", "data": {"text": body}})
    event: dict[str, object] = {
        "time": int(now.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "group" if group_id else "private",
        "message_id": message_id,
        "user_id": int(user_qq),
        "message": message,
    }
    if group_id:
        event["group_id"] = int(group_id)
    return event


class NetworkTripwire:
    """Only this injected fake may be called by the acceptance harness."""

    def __init__(self) -> None:
        self.requests: list[ChatGenerationRequest] = []

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.requests.append(request)
        return ChatGenerationResult(
            text="收到，我会认真回应。",
            provider_request_id="synthetic-provider",
            input_tokens=20,
            output_tokens=8,
        )

    async def close(self) -> None:
        return None


def all_open_gates(max_mode: ReplyActivationMode = ReplyActivationMode.AUTO) -> ReplyRuntimeGates:
    return ReplyRuntimeGates(
        worker_enabled=True,
        max_mode=max_mode,
        chat_model_configured=True,
        chat_route_enabled=True,
        model_network_enabled=True,
        chat_circuit_available=True,
        outbound_enabled=True,
        outbound_worker_active=True,
        onebot_token_configured=True,
        onebot_connected=True,
    )


async def runtime_harness(
    tmp_path: Path,
    mode: ReplyActivationMode,
    *,
    message_id: int,
    eligible: bool = False,
    approval_ttl_seconds: int = 1800,
    sender_qq: str = USER_QQ,
    group_id: str | None = None,
    mention_bot: bool = False,
    text: str | None = None,
    include_face: bool = False,
):
    repository = SQLiteRepository(tmp_path / f"runtime-{mode.value}-{message_id}.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    message = parse_message_event(
        onebot_event(
            message_id,
            now,
            user_qq=sender_qq,
            group_id=group_id,
            mention_bot=mention_bot,
            text=text,
            include_face=include_face,
        ),
        expected_bot_qq=BOT_QQ,
    )
    assert await repository.store_inbound(message)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    await orchestration.register_message(message, observed_at=now)
    control = ReplyRuntimeControlService(
        repository,
        bot_qq=BOT_QQ,
        owner_qq=OWNER_QQ,
        gates_provider=all_open_gates,
    )
    initial = await repository.ensure_reply_runtime_state(BOT_QQ)
    unpaused = await repository.compare_and_set_reply_runtime(
        bot_qq=BOT_QQ,
        expected_revision=initial["revision"],
        requested_mode=mode,
        emergency_paused=False,
        actor=OWNER_QQ,
        source="test",
        event_type="mode_changed",
    )
    assert unpaused is not None
    if eligible:
        item = await repository.set_reply_eligibility(
            bot_qq=BOT_QQ,
            conversation_key=message.conversation_key,
            enabled=True,
            expected_revision=0,
            actor=OWNER_QQ,
            source="test",
        )
        assert item is not None
    gateway = NetworkTripwire()
    assembler = ProvenanceContextAssembler(
        repository,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
    )
    runtime_holder: dict[str, ProductionReplyRuntimeService] = {}

    async def gated_before_model(run_id: str, lease_token: str) -> bool:
        return await runtime_holder["runtime"].before_model(run_id, lease_token)

    generation = FakeFirstReplyWorker(
        repository,
        orchestration,
        assembler,
        gateway,
        enabled=True,
        before_model=gated_before_model,
    )
    outbox = ReplyOutboxService(repository)
    runtime = ProductionReplyRuntimeService(
        repository,
        bot_qq=BOT_QQ,
        owner_qq=OWNER_QQ,
        control=control,
        generation_worker=generation,
        planner=ReplyPlanService(repository),
        outbox=outbox,
        approval_ttl_seconds=approval_ttl_seconds,
    )
    runtime_holder["runtime"] = runtime
    return repository, control, runtime, gateway, message, now


@pytest.mark.asyncio
async def test_runtime_modes_are_fail_closed_and_never_use_real_network(tmp_path: Path) -> None:
    observe = await runtime_harness(tmp_path, ReplyActivationMode.OBSERVE_ONLY, message_id=6101)
    observe_result = await observe[2].run_once(worker_id="observe", now=observe[5])
    assert observe_result.status == "paused"
    assert observe[3].requests == []

    shadow = await runtime_harness(
        tmp_path, ReplyActivationMode.SHADOW, message_id=6102, sender_qq=OWNER_QQ
    )
    shadow_result = await shadow[2].run_once(worker_id="shadow", now=shadow[5])
    assert shadow_result.stage == "shadow_completed"
    assert len(shadow[3].requests) == 1
    assert (await shadow[0].counts())["outbox"] == 0

    approval = await runtime_harness(
        tmp_path, ReplyActivationMode.OWNER_APPROVED, message_id=6103, sender_qq=OWNER_QQ
    )
    approval_result = await approval[2].run_once(worker_id="approval", now=approval[5])
    assert approval_result.stage == "awaiting_approval"
    pending = await approval[0].reply_runtime_approvals(BOT_QQ, status="pending")
    assert len(pending) == 1
    assert (await approval[0].counts())["outbox"] == 0
    approved = await approval[1].owner_action(
        actor_qq=OWNER_QQ,
        action="decide_approval",
        payload={"approval_id": pending[0]["id"], "approve": True},
    )
    assert approved["approval"]["status"] == "approved"
    assert (await approval[0].counts())["outbox"] > 0

    denied = await runtime_harness(
        tmp_path, ReplyActivationMode.LIMITED_AUTO, message_id=6104, sender_qq=OWNER_QQ
    )
    denied_result = await denied[2].run_once(worker_id="limited-denied", now=denied[5])
    assert denied_result.stage == "shadow_completed"
    assert len(denied[3].requests) == 1
    assert (await denied[0].counts())["outbox"] == 0

    allowed = await runtime_harness(
        tmp_path,
        ReplyActivationMode.LIMITED_AUTO,
        message_id=6105,
        eligible=True,
        sender_qq=OWNER_QQ,
    )
    allowed_result = await allowed[2].run_once(worker_id="limited-allowed", now=allowed[5])
    assert allowed_result.stage == "awaiting_delivery"
    assert len(allowed[3].requests) == 1
    assert (await allowed[0].counts())["outbox"] > 0
    assert (await allowed[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []

    automatic = await runtime_harness(
        tmp_path, ReplyActivationMode.AUTO, message_id=6106, sender_qq=AUTO_TEST_QQ
    )
    automatic_result = await automatic[2].run_once(worker_id="auto", now=automatic[5])
    assert automatic_result.stage == "awaiting_delivery"
    assert (await automatic[0].counts())["outbox"] > 0
    auto_detail = await automatic[0].reply_run_detail(automatic_result.run_id)
    assert auto_detail is not None
    assert auto_detail["conversation_key"] == f"{BOT_QQ}:private:{AUTO_TEST_QQ}"
    assert (await automatic[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []
    await automatic[1].owner_action(actor_qq=OWNER_QQ, action="emergency_stop", payload={})
    assert await automatic[0].claim_outbound() is None
    await automatic[1].owner_action(actor_qq=OWNER_QQ, action="resume", payload={})
    assert await automatic[0].claim_outbound() is not None


@pytest.mark.asyncio
async def test_shadow_does_not_call_model_for_non_owner_private(tmp_path: Path) -> None:
    foreign = await runtime_harness(
        tmp_path, ReplyActivationMode.SHADOW, message_id=6109, sender_qq=USER_QQ
    )
    result = await foreign[2].run_once(worker_id="shadow-foreign", now=foreign[5])
    assert foreign[3].requests == []
    assert (await foreign[0].counts())["outbox"] == 0
    assert result.status == "suppressed"
    assert result.stage == "suppressed"
    assert result.run_id is not None
    detail = await foreign[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "conversation_ineligible"
    assert detail["conversation_key"] == f"{BOT_QQ}:private:{USER_QQ}"


@pytest.mark.asyncio
async def test_owner_approved_does_not_call_model_for_non_owner_private(tmp_path: Path) -> None:
    foreign = await runtime_harness(
        tmp_path, ReplyActivationMode.OWNER_APPROVED, message_id=6110, sender_qq=USER_QQ
    )
    result = await foreign[2].run_once(worker_id="approved-foreign", now=foreign[5])
    assert foreign[3].requests == []
    assert (await foreign[0].counts())["outbox"] == 0
    assert result.status == "suppressed"
    assert result.run_id is not None
    detail = await foreign[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "conversation_ineligible"
    assert (await foreign[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []


@pytest.mark.asyncio
async def test_limited_auto_does_not_call_model_for_non_owner_private(tmp_path: Path) -> None:
    foreign = await runtime_harness(
        tmp_path,
        ReplyActivationMode.LIMITED_AUTO,
        message_id=6111,
        eligible=True,
        sender_qq=USER_QQ,
    )
    result = await foreign[2].run_once(worker_id="limited-foreign", now=foreign[5])
    assert foreign[3].requests == []
    assert (await foreign[0].counts())["outbox"] == 0
    assert result.status == "suppressed"
    assert result.run_id is not None
    detail = await foreign[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "conversation_ineligible"
    assert (await foreign[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []


@pytest.mark.asyncio
async def test_auto_sends_for_any_private(tmp_path: Path) -> None:
    foreign = await runtime_harness(
        tmp_path, ReplyActivationMode.AUTO, message_id=6112, sender_qq=USER_QQ
    )
    result = await foreign[2].run_once(worker_id="auto-any-private", now=foreign[5])
    assert result.stage == "awaiting_delivery"
    assert len(foreign[3].requests) == 1
    assert (await foreign[0].counts())["outbox"] > 0
    detail = await foreign[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["conversation_key"] == f"{BOT_QQ}:private:{USER_QQ}"
    assert (await foreign[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []


@pytest.mark.asyncio
async def test_auto_sends_for_owner_private_without_approval(tmp_path: Path) -> None:
    owner = await runtime_harness(
        tmp_path, ReplyActivationMode.AUTO, message_id=6113, sender_qq=OWNER_QQ
    )
    result = await owner[2].run_once(worker_id="auto-owner", now=owner[5])
    assert result.stage == "awaiting_delivery"
    assert len(owner[3].requests) == 1
    assert (await owner[0].counts())["outbox"] > 0
    assert (await owner[0].reply_runtime_approvals(BOT_QQ, status="pending")) == []


@pytest.mark.asyncio
async def test_auto_sends_for_named_group_mention(tmp_path: Path) -> None:
    named = await runtime_harness(
        tmp_path,
        ReplyActivationMode.AUTO,
        message_id=6114,
        sender_qq=OWNER_QQ,
        group_id=AUTO_TEST_GROUP,
        mention_bot=True,
    )
    result = await named[2].run_once(worker_id="auto-named-group", now=named[5])
    assert result.stage == "awaiting_delivery"
    assert len(named[3].requests) == 1
    assert (await named[0].counts())["outbox"] > 0
    detail = await named[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["conversation_key"] == f"{BOT_QQ}:group:{AUTO_TEST_GROUP}"
    outbox = await named[0].claim_outbound()
    assert outbox is not None
    assert outbox.target_id == AUTO_TEST_GROUP
    assert outbox.conversation_kind.value == "group"


@pytest.mark.asyncio
async def test_auto_sends_for_named_group_mention_with_only_a_face(tmp_path: Path) -> None:
    named = await runtime_harness(
        tmp_path,
        ReplyActivationMode.AUTO,
        message_id=6117,
        sender_qq=OWNER_QQ,
        group_id=AUTO_TEST_GROUP,
        mention_bot=True,
        include_face=True,
    )
    result = await named[2].run_once(worker_id="auto-named-group-face", now=named[5])
    assert result.stage == "awaiting_delivery"
    assert len(named[3].requests) == 1
    assert named[3].requests[0].messages[-1].content == "[表情]"
    assert (await named[0].counts())["outbox"] > 0


@pytest.mark.asyncio
async def test_auto_does_not_call_model_for_named_group_without_mention(
    tmp_path: Path,
) -> None:
    silent = await runtime_harness(
        tmp_path,
        ReplyActivationMode.AUTO,
        message_id=6115,
        sender_qq=OWNER_QQ,
        group_id=AUTO_TEST_GROUP,
        mention_bot=False,
    )
    result = await silent[2].run_once(worker_id="auto-group-silent", now=silent[5])
    assert silent[3].requests == []
    assert (await silent[0].counts())["outbox"] == 0
    assert result.status == "suppressed"
    detail = await silent[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["failure_code"] == "group_not_mentioned"


@pytest.mark.asyncio
async def test_auto_sends_for_any_group_mention(tmp_path: Path) -> None:
    other = await runtime_harness(
        tmp_path,
        ReplyActivationMode.AUTO,
        message_id=6116,
        sender_qq=OWNER_QQ,
        group_id="111111111",
        mention_bot=True,
    )
    result = await other[2].run_once(worker_id="auto-any-group", now=other[5])
    assert result.stage == "awaiting_delivery"
    assert len(other[3].requests) == 1
    assert (await other[0].counts())["outbox"] > 0
    detail = await other[0].reply_run_detail(result.run_id)
    assert detail is not None
    assert detail["conversation_key"] == f"{BOT_QQ}:group:111111111"
    outbox = await other[0].claim_outbound()
    assert outbox is not None
    assert outbox.target_id == "111111111"
    assert outbox.conversation_kind.value == "group"


@pytest.mark.asyncio
async def test_runtime_cas_preview_staleness_and_exact_bot_scope(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "runtime-controls.sqlite3")
    await repository.initialize()
    control = ReplyRuntimeControlService(
        repository,
        bot_qq=BOT_QQ,
        owner_qq=OWNER_QQ,
        gates_provider=all_open_gates,
    )
    initial = await repository.ensure_reply_runtime_state(BOT_QQ)
    assert initial["requested_mode"] == "observe_only"
    assert initial["emergency_paused"] is True
    preview = await control.preview(action="resume", payload={})
    changed = await repository.compare_and_set_reply_runtime(
        bot_qq=BOT_QQ,
        expected_revision=initial["revision"],
        requested_mode=ReplyActivationMode.SHADOW,
        actor=OWNER_QQ,
        source="test",
        event_type="mode_changed",
    )
    assert changed is not None
    with pytest.raises(ReplyRuntimeControlError, match="stale"):
        await control.confirm(token=preview["confirmation_token"], actor=OWNER_QQ)
    assert (
        await repository.compare_and_set_reply_runtime(
            bot_qq=BOT_QQ,
            expected_revision=initial["revision"],
            requested_mode=ReplyActivationMode.AUTO,
            actor=OWNER_QQ,
            source="test",
            event_type="mode_changed",
        )
        is None
    )
    with pytest.raises(ValueError, match="does not belong"):
        await repository.set_reply_eligibility(
            bot_qq=BOT_QQ,
            conversation_key="999999999:private:123456789",
            enabled=True,
            expected_revision=0,
            actor=OWNER_QQ,
            source="test",
        )


@pytest.mark.asyncio
async def test_expired_approval_is_suppressed_without_delivery(tmp_path: Path) -> None:
    harness = await runtime_harness(
        tmp_path,
        ReplyActivationMode.OWNER_APPROVED,
        message_id=6201,
        approval_ttl_seconds=60,
        sender_qq=OWNER_QQ,
    )
    repository, _control, runtime, _gateway, _message, now = harness
    awaiting = await runtime.run_once(worker_id="approval", now=now)
    assert awaiting.run_id is not None
    result = await runtime.run_once(worker_id="expiry", now=now + timedelta(seconds=61))
    assert result.status == "idle"
    detail = await repository.reply_run_detail(awaiting.run_id)
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "approval_expired"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_owner_can_explicitly_cancel_revision_bound_approval(tmp_path: Path) -> None:
    repository, control, runtime, _gateway, _message, now = await runtime_harness(
        tmp_path, ReplyActivationMode.OWNER_APPROVED, message_id=6202, sender_qq=OWNER_QQ
    )
    awaiting = await runtime.run_once(worker_id="approval-cancel", now=now)
    pending = await repository.reply_runtime_approvals(BOT_QQ, status="pending")
    result = await control.owner_action(
        actor_qq=OWNER_QQ,
        action="cancel_approval",
        payload={"approval_id": pending[0]["id"]},
    )
    assert result["approval"]["status"] == "cancelled"
    detail = await repository.reply_run_detail(awaiting.run_id)
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "owner_cancelled"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_worker_crash_stops_loop_and_persists_sanitized_failure(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "runtime-crash.sqlite3")
    await repository.initialize()

    class CrashingRuntime:
        control = None

        async def run_once(self, *, worker_id: str):
            del worker_id
            raise RuntimeError("secret provider body must not persist")

    worker = ReplyRuntimeWorker(
        repository,
        CrashingRuntime(),
        bot_qq=BOT_QQ,
        configured_enabled=True,
        poll_seconds=0.1,
    )
    with pytest.raises(RuntimeError):
        await worker.run_forever()
    state = await repository.reply_runtime_state(BOT_QQ)
    assert state["lifecycle"] == "failed"
    assert state["last_failure_code"] == "worker_failed"
    assert "secret" not in str(state)


@pytest.mark.asyncio
async def test_concurrent_runtime_revisions_have_one_winner(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "runtime-race.sqlite3")
    await repository.initialize()
    initial = await repository.ensure_reply_runtime_state(BOT_QQ)

    async def update(mode: ReplyActivationMode):
        return await repository.compare_and_set_reply_runtime(
            bot_qq=BOT_QQ,
            expected_revision=initial["revision"],
            requested_mode=mode,
            actor=OWNER_QQ,
            source="race-test",
            event_type="mode_changed",
        )

    results = await asyncio.gather(
        update(ReplyActivationMode.SHADOW), update(ReplyActivationMode.OWNER_APPROVED)
    )
    assert sum(item is not None for item in results) == 1


@pytest.mark.asyncio
async def test_v28_reply_run_table_migrates_with_data_and_foreign_keys_intact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime-v28-upgrade.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    message = parse_message_event(onebot_event(6250, now), expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=30)
    registered = await orchestration.register_message(message, observed_at=now)
    run_id = registered["run"]["id"]
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.executescript(
            """
            DROP INDEX idx_reply_runs_one_active_conversation;
            DROP INDEX idx_reply_runs_stage_updated;
            DROP INDEX idx_reply_runs_conversation_created;
            DROP INDEX idx_reply_runs_subject_created;
            ALTER TABLE reply_runs RENAME TO reply_runs_v29_test;
            CREATE TABLE reply_runs (
                id TEXT PRIMARY KEY,
                bot_qq TEXT NOT NULL,
                conversation_key TEXT NOT NULL
                    REFERENCES conversations(conversation_key) ON DELETE CASCADE,
                conversation_kind TEXT NOT NULL CHECK(conversation_kind IN ('private', 'group')),
                subject_user_qq TEXT,
                stage TEXT NOT NULL CHECK(stage IN (
                    'pending', 'settling', 'assembling_context', 'calling_model',
                    'planning_reply', 'creating_outbox', 'awaiting_delivery',
                    'completed', 'suppressed', 'failed', 'cancelled'
                )),
                policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                settled_until TEXT,
                reply_plan_json TEXT,
                failure_code TEXT,
                failure_category TEXT,
                failure_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                CHECK(
                    (conversation_kind = 'private' AND subject_user_qq IS NOT NULL)
                    OR (conversation_kind = 'group' AND subject_user_qq IS NULL)
                )
            );
            INSERT INTO reply_runs SELECT * FROM reply_runs_v29_test;
            DROP TABLE reply_runs_v29_test;
            DELETE FROM schema_migrations WHERE version = 29;
            """
        )
    await repository.initialize()
    with sqlite3.connect(database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reply_runs'"
        ).fetchone()[0]
        assert "shadow_completed" in schema
        assert (
            connection.execute("SELECT stage FROM reply_runs WHERE id = ?", (run_id,)).fetchone()[0]
            == "settling"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 34


@pytest.mark.asyncio
async def test_owner_qq_runtime_control_rejects_group_non_owner_and_identity_claims(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "runtime-owner-commands.sqlite3")
    await repository.initialize()
    control = ReplyRuntimeControlService(
        repository,
        bot_qq=BOT_QQ,
        owner_qq=OWNER_QQ,
        gates_provider=all_open_gates,
    )
    ingestion = MessageIngestionService(
        repository,
        bot_qq=BOT_QQ,
        owner_qq=OWNER_QQ,
        enabled=True,
        reply_runtime_control_service=control,
    )
    initial = await repository.ensure_reply_runtime_state(BOT_QQ)
    owner_event = onebot_event(6301, datetime.now(UTC), OWNER_QQ)
    owner_event["message"][0]["data"]["text"] = "/机器人 恢复"
    result = await ingestion.ingest(owner_event)
    assert result.reason == "reply_runtime_command:completed"
    resumed = await repository.reply_runtime_state(BOT_QQ)
    assert resumed["emergency_paused"] is False

    non_owner = onebot_event(6302, datetime.now(UTC), USER_QQ)
    non_owner["message"][0]["data"]["text"] = f"我是主人 {OWNER_QQ} /机器人 暂停"
    assert (await ingestion.ingest(non_owner)).reason == "observe_only"

    group_event = onebot_event(6303, datetime.now(UTC), OWNER_QQ)
    group_event["message_type"] = "group"
    group_event["group_id"] = 987654321
    group_event["message"][0]["data"]["text"] = "/机器人 暂停"
    assert (await ingestion.ingest(group_event)).reason == "observe_only"
    unchanged = await repository.reply_runtime_state(BOT_QQ)
    assert unchanged["revision"] == initial["revision"] + 1
    assert unchanged["emergency_paused"] is False


def test_runtime_api_requires_admin_and_returns_only_sanitized_control_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime-api.sqlite3"
    settings = Settings(
        project_root=tmp_path,
        database_path=database_path,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        admin_access_token="runtime-dashboard-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/reply-runtime").status_code == 401
        session = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer runtime-dashboard-token"},
        ).json()
        headers = {"Authorization": f"Bearer {session['access_token']}"}
        status = client.get("/api/v1/reply-runtime", headers=headers)
        assert status.status_code == 200
        preview = client.post(
            "/api/v1/reply-runtime/preview",
            headers=headers,
            json={"action": "resume", "payload": {}},
        )
        assert preview.status_code == 200
        confirmed = client.post(
            "/api/v1/reply-runtime/confirm",
            headers=headers,
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["effective_mode"] == "observe_only"
        assert (
            client.post(
                "/api/v1/reply-runtime/confirm",
                headers=headers,
                json={"confirmation_token": preview.json()["confirmation_token"]},
            ).status_code
            == 409
        )
        serialized = str(confirmed.json()).lower()
        for forbidden in ("api_key", "lease_token", "prompt", "reply_text", "raw_payload"):
            assert forbidden not in serialized
