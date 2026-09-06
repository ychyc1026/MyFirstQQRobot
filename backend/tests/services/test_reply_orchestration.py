import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.application import ReplyOrchestrationService
from ych_bot.domain import ReplyFailure, ReplyFailureCategory, ReplyRunStage
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event


def event(message_id: int, user_id: int = 123456789) -> dict:
    return {
        "time": 1_700_000_000 + message_id,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": f"message-{message_id}"}}],
    }


async def store_message(repository: SQLiteRepository, message_id: int, user_id: int = 123456789):
    message = parse_message_event(event(message_id, user_id), expected_bot_qq="2000000002")
    assert await repository.store_inbound(message)
    return message


@pytest.mark.asyncio
async def test_burst_window_extends_and_survives_service_restart(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "orchestration.sqlite3")
    await repository.initialize()
    service = ReplyOrchestrationService(repository, settle_seconds=2)
    base = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    first = await store_message(repository, 501)

    registered = await service.register_message(first, observed_at=base)

    run_id = registered["run"]["id"]
    assert registered["run"]["stage"] == "settling"
    assert await service.claim_ready(worker_id="worker-a", now=base + timedelta(seconds=1)) is None

    second = await store_message(repository, 502)
    appended = await service.register_message(second, observed_at=base + timedelta(seconds=1))
    assert appended["run"]["id"] == run_id
    assert appended["trigger_added"] is True
    assert (
        await service.claim_ready(worker_id="worker-a", now=base + timedelta(seconds=2.5)) is None
    )

    restarted_service = ReplyOrchestrationService(repository, settle_seconds=2)
    claimed = await restarted_service.claim_ready(
        worker_id="worker-after-restart",
        now=base + timedelta(seconds=3),
    )

    assert claimed is not None
    assert claimed["id"] == run_id
    assert claimed["stage"] == "assembling_context"
    assert claimed["lease"]["lease_owner"] == "worker-after-restart"
    detail = await repository.reply_run_detail(run_id)
    assert [item["message_id"] for item in detail["triggers"]] == [first.id, second.id]

    third = await store_message(repository, 503)
    deferred = await restarted_service.register_message(
        third, observed_at=base + timedelta(seconds=3.1)
    )
    assert deferred["deferred"] is True
    assert deferred["trigger_added"] is False

    failure = ReplyFailure(
        code="test_stop",
        category=ReplyFailureCategory.INTERNAL,
        retryable=False,
        safe_detail="test completed active run",
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
        target_stage=ReplyRunStage.FAILED,
        failure=failure,
    )
    successor = await restarted_service.register_message(
        third, observed_at=base + timedelta(seconds=4)
    )
    assert successor["created"] is False
    assert successor["trigger_added"] is False
    assert successor["run"]["id"] != run_id
    assert successor["run"]["stage"] == "settling"
    successor_detail = await repository.reply_run_detail(successor["run"]["id"])
    assert [item["message_id"] for item in successor_detail["triggers"]] == [third.id]


@pytest.mark.asyncio
async def test_concurrent_workers_claim_ready_run_only_once(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "orchestration-claim.sqlite3")
    await repository.initialize()
    service = ReplyOrchestrationService(repository, settle_seconds=0)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    message = await store_message(repository, 601)
    await service.register_message(message, observed_at=now)

    claims = await asyncio.gather(
        service.claim_ready(worker_id="worker-a", now=now),
        service.claim_ready(worker_id="worker-b", now=now),
    )

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["stage"] == "assembling_context"


@pytest.mark.asyncio
async def test_ready_runs_from_separate_conversations_are_independent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "orchestration-isolation.sqlite3")
    await repository.initialize()
    service = ReplyOrchestrationService(repository, settle_seconds=0)
    now = datetime(2026, 8, 28, 13, 0, tzinfo=UTC)
    first = await store_message(repository, 701, user_id=111111111)
    second = await store_message(repository, 702, user_id=222222222)
    await service.register_message(first, observed_at=now)
    await service.register_message(second, observed_at=now)

    first_claim = await service.claim_ready(worker_id="worker-a", now=now)
    second_claim = await service.claim_ready(worker_id="worker-b", now=now)

    assert first_claim is not None and second_claim is not None
    assert first_claim["conversation_key"] != second_claim["conversation_key"]
    assert {first_claim["subject_user_qq"], second_claim["subject_user_qq"]} == {
        "111111111",
        "222222222",
    }
