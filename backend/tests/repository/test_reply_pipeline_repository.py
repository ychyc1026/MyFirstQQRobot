import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.domain import (
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    ConversationKind,
    MessageEnvelope,
    MessageSegment,
    OutboundMessage,
    ReplyFailure,
    ReplyFailureCategory,
    ReplyRunStage,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event


def event(message_id: int, text: str) -> dict:
    return {
        "time": 1_700_000_000 + message_id,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": 123456789,
        "message": [{"type": "text", "data": {"text": text}}],
    }


async def stored_envelope(repository: SQLiteRepository, message_id: int) -> MessageEnvelope:
    message = parse_message_event(
        event(message_id, f"message-{message_id}"), expected_bot_qq="2000000002"
    )
    assert await repository.store_inbound(message) is True
    return MessageEnvelope.from_unified(message)


@pytest.mark.asyncio
async def test_create_or_append_run_is_idempotent_and_defers_after_execution(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-run.sqlite3")
    await repository.initialize()
    first = await stored_envelope(repository, 101)
    second = await stored_envelope(repository, 102)
    settle = datetime.now(UTC) + timedelta(seconds=2)

    created = await repository.create_or_append_reply_run(
        run_id="run-1", envelope=first, settled_until=settle, policy_snapshot={"mode": "fake"}
    )
    duplicate = await repository.create_or_append_reply_run(
        run_id="ignored-run-id",
        envelope=first,
        settled_until=settle,
        policy_snapshot={"mode": "changed"},
    )
    appended = await repository.create_or_append_reply_run(
        run_id="also-ignored", envelope=second, settled_until=settle, policy_snapshot={}
    )

    assert created["created"] is True and created["trigger_added"] is True
    assert duplicate["created"] is False and duplicate["trigger_added"] is False
    assert appended["run"]["id"] == "run-1" and appended["trigger_added"] is True
    assert appended["run"]["policy_snapshot"] == {"mode": "fake"}
    assert [
        item["message_id"] for item in (await repository.reply_run_detail("run-1"))["triggers"]
    ] == [
        first.message_id,
        second.message_id,
    ]

    assert await repository.transition_reply_run(
        run_id="run-1",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SETTLING,
    )
    assert await repository.transition_reply_run(
        run_id="run-1",
        expected_stage=ReplyRunStage.SETTLING,
        target_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
    )
    third = await stored_envelope(repository, 103)
    deferred = await repository.create_or_append_reply_run(
        run_id="run-2", envelope=third, settled_until=settle, policy_snapshot={}
    )
    assert deferred["deferred"] is True
    assert deferred["trigger_added"] is False
    with pytest.raises(ValueError, match="durable message evidence"):
        await repository.create_or_append_reply_run(
            run_id="forged-run",
            envelope=replace(third, event_key="forged-event"),
            settled_until=settle,
            policy_snapshot={},
        )


@pytest.mark.asyncio
async def test_concurrent_first_triggers_share_one_active_conversation_run(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-concurrent.sqlite3")
    await repository.initialize()
    first = await stored_envelope(repository, 111)
    second = await stored_envelope(repository, 112)
    settle = datetime.now(UTC) + timedelta(seconds=2)

    results = await asyncio.gather(
        repository.create_or_append_reply_run(
            run_id="concurrent-a", envelope=first, settled_until=settle, policy_snapshot={}
        ),
        repository.create_or_append_reply_run(
            run_id="concurrent-b", envelope=second, settled_until=settle, policy_snapshot={}
        ),
    )

    assert sum(int(result["created"]) for result in results) == 1
    run_ids = {result["run"]["id"] for result in results}
    assert len(run_ids) == 1
    detail = await repository.reply_run_detail(run_ids.pop())
    assert len(detail["triggers"]) == 2


@pytest.mark.asyncio
async def test_compare_and_set_transition_rejects_stale_and_illegal_updates(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-cas.sqlite3")
    await repository.initialize()
    envelope = await stored_envelope(repository, 201)
    await repository.create_or_append_reply_run(
        run_id="run-cas",
        envelope=envelope,
        settled_until=datetime.now(UTC),
        policy_snapshot={},
    )

    assert await repository.transition_reply_run(
        run_id="run-cas",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SETTLING,
    )
    assert not await repository.transition_reply_run(
        run_id="run-cas",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SUPPRESSED,
    )
    with pytest.raises(ValueError, match="illegal"):
        await repository.transition_reply_run(
            run_id="run-cas",
            expected_stage=ReplyRunStage.SETTLING,
            target_stage=ReplyRunStage.CALLING_MODEL,
        )

    failure = ReplyFailure(
        code="policy_denied",
        category=ReplyFailureCategory.POLICY_DENIED,
        retryable=False,
        safe_detail="reply policy denied",
    )
    assert await repository.transition_reply_run(
        run_id="run-cas",
        expected_stage=ReplyRunStage.SETTLING,
        target_stage=ReplyRunStage.SUPPRESSED,
        failure=failure,
    )
    detail = await repository.reply_run_detail("run-cas")
    assert detail["stage"] == "suppressed"
    assert detail["failure_code"] == "policy_denied"
    assert detail["completed_at"] is not None


@pytest.mark.asyncio
async def test_reply_run_lease_can_only_be_reclaimed_after_expiry(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-lease.sqlite3")
    await repository.initialize()
    envelope = await stored_envelope(repository, 301)
    await repository.create_or_append_reply_run(
        run_id="run-lease",
        envelope=envelope,
        settled_until=datetime.now(UTC),
        policy_snapshot={},
    )
    base = datetime.now(UTC)

    assert await repository.acquire_reply_run_lease(
        run_id="run-lease",
        lease_owner="worker-a",
        lease_token="token-a",
        ttl_seconds=10,
        now=base,
    )
    assert not await repository.acquire_reply_run_lease(
        run_id="run-lease",
        lease_owner="worker-b",
        lease_token="token-b",
        ttl_seconds=10,
        now=base + timedelta(seconds=9),
    )
    assert not await repository.transition_reply_run(
        run_id="run-lease",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SETTLING,
        lease_token="wrong-token",
        now=base,
    )
    assert not await repository.transition_reply_run(
        run_id="run-lease",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SETTLING,
        lease_token="token-a",
        now=base + timedelta(seconds=11),
    )
    assert not await repository.heartbeat_reply_run_lease(
        run_id="run-lease", lease_token="wrong-token", ttl_seconds=10
    )
    assert await repository.heartbeat_reply_run_lease(
        run_id="run-lease", lease_token="token-a", ttl_seconds=10
    )
    assert await repository.acquire_reply_run_lease(
        run_id="run-lease",
        lease_owner="worker-b",
        lease_token="token-b",
        ttl_seconds=10,
        now=base + timedelta(seconds=11),
    )
    assert await repository.transition_reply_run(
        run_id="run-lease",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SETTLING,
        lease_token="token-b",
        now=base + timedelta(seconds=11),
    )
    assert not await repository.release_reply_run_lease(run_id="run-lease", lease_token="token-a")
    assert await repository.release_reply_run_lease(run_id="run-lease", lease_token="token-b")


@pytest.mark.asyncio
async def test_terminal_transition_releases_current_lease(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-terminal-lease.sqlite3")
    await repository.initialize()
    envelope = await stored_envelope(repository, 302)
    await repository.create_or_append_reply_run(
        run_id="run-terminal-lease",
        envelope=envelope,
        settled_until=datetime.now(UTC),
        policy_snapshot={},
    )
    assert await repository.acquire_reply_run_lease(
        run_id="run-terminal-lease",
        lease_owner="worker-a",
        lease_token="terminal-token",
        ttl_seconds=30,
    )
    failure = ReplyFailure(
        code="policy_denied",
        category=ReplyFailureCategory.POLICY_DENIED,
        retryable=False,
        safe_detail="suppressed by policy",
    )

    assert await repository.transition_reply_run(
        run_id="run-terminal-lease",
        expected_stage=ReplyRunStage.PENDING,
        target_stage=ReplyRunStage.SUPPRESSED,
        failure=failure,
    )

    detail = await repository.reply_run_detail("run-terminal-lease")
    assert detail["lease"] is None
    assert not await repository.acquire_reply_run_lease(
        run_id="run-terminal-lease",
        lease_owner="worker-b",
        lease_token="new-token",
        ttl_seconds=30,
    )


@pytest.mark.asyncio
async def test_manifest_and_delivery_evidence_are_idempotent_and_visible(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-evidence.sqlite3")
    await repository.initialize()
    envelope = await stored_envelope(repository, 401)
    await repository.create_or_append_reply_run(
        run_id="run-evidence",
        envelope=envelope,
        settled_until=datetime.now(UTC),
        policy_snapshot={},
    )
    manifest = ContextManifest(
        run_id="run-evidence",
        conversation_key=envelope.conversation_key,
        sections=(
            ContextSection(
                section_id="identity",
                source_class=ContextSourceClass.CORE_IDENTITY,
                scope=ContextScope.GLOBAL,
                content="YCH",
                policy_decision=ContextPolicyDecision.ALLOWED,
            ),
        ),
    )
    assert await repository.acquire_reply_run_lease(
        run_id="run-evidence",
        lease_owner="context-worker",
        lease_token="context-token",
        ttl_seconds=30,
    )
    assert not await repository.store_reply_context_manifest(
        manifest_id="manifest-wrong-lease",
        manifest=manifest,
        revision=1,
        rendered_content="YCH",
        budget_chars=100,
        lease_token="wrong-token",
    )
    assert await repository.store_reply_context_manifest(
        manifest_id="manifest-1",
        manifest=manifest,
        revision=1,
        rendered_content="YCH",
        budget_chars=100,
        lease_token="context-token",
    )
    assert not await repository.store_reply_context_manifest(
        manifest_id="manifest-duplicate",
        manifest=manifest,
        revision=1,
        rendered_content="YCH",
        budget_chars=100,
    )
    wrong_manifest = ContextManifest(
        run_id="run-evidence",
        conversation_key="2000000002:private:other-user",
        sections=manifest.sections,
    )
    assert not await repository.store_reply_context_manifest(
        manifest_id="manifest-wrong-conversation",
        manifest=wrong_manifest,
        revision=2,
        rendered_content="YCH",
        budget_chars=100,
    )
    outbound = OutboundMessage(
        id="outbox-1",
        idempotency_key="run-evidence:1",
        conversation_kind=ConversationKind.PRIVATE,
        target_id=envelope.peer_id,
        segments=(MessageSegment("text", {"text": "reply"}),),
    )
    assert await repository.enqueue_outbound(outbound)
    evidence_args = {
        "evidence_id": "evidence-1",
        "run_id": "run-evidence",
        "outbox_id": "outbox-1",
        "bubble_sequence": 1,
        "attempt": 1,
        "idempotency_key": "run-evidence:1",
        "outcome": "queued",
        "evidence": {"source": "fake"},
    }
    assert await repository.record_reply_delivery_evidence(**evidence_args)
    assert not await repository.record_reply_delivery_evidence(**evidence_args)
    assert not await repository.record_reply_delivery_evidence(
        **{**evidence_args, "evidence_id": "evidence-wrong", "idempotency_key": "wrong-key"}
    )

    detail = await repository.reply_run_detail("run-evidence")
    assert detail["context_manifests"][0]["manifest"]["sections"][0]["content"] == "YCH"
    assert detail["delivery_evidence"][0]["evidence"] == {"source": "fake"}
