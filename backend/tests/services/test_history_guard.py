from pathlib import Path

import httpx
import pytest
from _support.readiness import ALLOW_READINESS, BlockReadinessGuard
from ych_bot.application.privacy import NapCatHistoryReader
from ych_bot.domain.identity import HistoryAccessMode
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient


@pytest.mark.asyncio
async def test_default_policy_blocks_napcat_before_network_call(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=httpx.MockTransport(handler))
    reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    try:
        result = await reader.read_private_history(
            user_qq="123456789",
            count=20,
            include_media=False,
            purpose="initialize_user_profile",
        )
    finally:
        await client.close()

    assert result.allowed is False
    assert result.reason == "history_mode_deny"
    assert calls == 0
    assert (await repository.counts())["data_access_log"] == 1


@pytest.mark.asyncio
async def test_one_time_authorization_is_consumed_atomically(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/get_friend_msg_history"
        return httpx.Response(
            200,
            json={"status": "ok", "retcode": 0, "data": {"messages": [{"id": "m1"}]}},
        )

    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    await repository.set_history_policy(
        user_qq="123456789",
        mode=HistoryAccessMode.ONE_TIME,
        max_messages=10,
        one_time_remaining=1,
        updated_by="2000000001",
        reason="approved:test",
    )
    client = NapCatClient("http://napcat.test", transport=httpx.MockTransport(handler))
    reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    try:
        first = await reader.read_private_history(
            user_qq="123456789",
            count=10,
            include_media=False,
            purpose="restore_recent_context",
        )
        second = await reader.read_private_history(
            user_qq="123456789",
            count=10,
            include_media=False,
            purpose="restore_recent_context",
        )
    finally:
        await client.close()

    assert first.allowed is True
    assert first.messages == ({"id": "m1"},)
    assert second.allowed is False
    assert second.reason == "one_time_authorization_consumed"
    assert calls == 1


@pytest.mark.asyncio
async def test_selected_range_is_rejected_when_source_cannot_enforce_range(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    await repository.set_history_policy(
        user_qq="123456789",
        mode=HistoryAccessMode.SELECTED_RANGE,
        selected_from="2026-08-01",
        selected_to="2026-08-10",
        max_messages=100,
        updated_by="2000000001",
        reason="approved:test",
    )
    client = NapCatClient(
        "http://napcat.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    try:
        result = await reader.read_private_history(
            user_qq="123456789",
            count=50,
            include_media=False,
            purpose="initialize_user_profile",
        )
    finally:
        await client.close()

    assert result.allowed is False
    assert result.reason == "source_cannot_enforce_selected_range"


@pytest.mark.asyncio
async def test_readiness_block_preserves_one_time_history_grant(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"messages": []}})

    repository = SQLiteRepository(tmp_path / "privacy-readiness.sqlite3")
    await repository.initialize()
    await repository.set_history_policy(
        user_qq="123456789",
        mode=HistoryAccessMode.ONE_TIME,
        max_messages=10,
        one_time_remaining=1,
        updated_by="2000000001",
        reason="approved:test",
    )
    client = NapCatClient("http://napcat.test", transport=httpx.MockTransport(handler))
    blocked_reader = NapCatHistoryReader(
        repository,
        client,
        readiness_guard=BlockReadinessGuard("history_not_ready"),  # type: ignore[arg-type]
    )
    allowed_reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    try:
        blocked = await blocked_reader.read_private_history(
            user_qq="123456789",
            count=10,
            include_media=False,
            purpose="restore_recent_context",
        )
        allowed = await allowed_reader.read_private_history(
            user_qq="123456789",
            count=10,
            include_media=False,
            purpose="restore_recent_context",
        )
    finally:
        await client.close()

    assert blocked.allowed is False
    assert blocked.reason == "readiness_history_not_ready"
    assert allowed.allowed is True
    assert calls == 1
