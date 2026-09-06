from pathlib import Path

import httpx
import pytest
from _support.owner import BOT_QQ, OWNER_QQ, friend_list_transport, owner_command
from _support.readiness import ALLOW_READINESS
from ych_bot.application.control import OwnerControlService
from ych_bot.application.privacy import NapCatHistoryReader
from ych_bot.domain.control import OwnerCommandKind
from ych_bot.domain.identity import HistoryAccessMode
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient


@pytest.mark.asyncio
async def test_owner_help_is_read_only_and_explains_safety_gates(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "help.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=friend_list_transport())
    service = OwnerControlService(repository, client, owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    try:
        result = await service.execute(owner_command(OwnerCommandKind.HELP))
        assert result.status == "completed"
        assert result.data["title"] == "YCH 主号控制帮助"
        assert "/确认 <审批码>" in result.data["high_risk"]
        assert "/用户 <QQ> 备注 <名字>" in result.data["controlled"]
        assert "docs/operator" in result.data["handbook"]
        assert "不会打开模型" in result.data["safety"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_owner_override_and_history_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "control.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=friend_list_transport())
    service = OwnerControlService(repository, client, owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    try:
        override = await service.execute(
            owner_command(
                OwnerCommandKind.USER_MARK_EXISTING_FRIEND,
                {"user_qq": "123456789"},
            )
        )
        assert override.status == "completed"
        detail = await repository.user_detail("123456789")
        assert detail["relationship"]["friend_state"] == "existing_friend"
        assert detail["relationship"]["state_source"] == "owner_override"

        request = await service.execute(
            owner_command(
                OwnerCommandKind.USER_HISTORY_SELECTED_RANGE,
                {
                    "user_qq": "123456789",
                    "selected_from": "2026-08-01",
                    "selected_to": "2026-08-10",
                    "max_messages": "200",
                },
            )
        )
        assert request.status == "pending_approval"
        pending = await repository.pending_approvals()
        assert pending[0]["payload"] == {
            "user_qq": "123456789",
            "selected_from": "2026-08-01",
            "selected_to": "2026-08-10",
            "max_messages": "200",
        }

        approval = await service.execute(
            owner_command(
                OwnerCommandKind.APPROVE,
                {"approval_code": request.data["approval_code"]},
            )
        )
        assert approval.data["approved"] is True
        detail = await repository.user_detail("123456789")
        assert detail["history_policy"]["mode"] == "selected_range"
        assert detail["history_policy"]["max_messages"] == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_friend_baseline_requires_preview_then_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "baseline.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=friend_list_transport())
    service = OwnerControlService(repository, client, owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    try:
        preview = await service.execute(owner_command(OwnerCommandKind.FRIEND_BASELINE_PREVIEW))
        assert preview.status == "pending_approval"
        assert preview.data["friend_count"] == 2

        before = await repository.identity_summary()
        assert before["latest_baseline"] is None

        approved = await service.execute(
            owner_command(
                OwnerCommandKind.APPROVE,
                {"approval_code": preview.data["approval_code"]},
            )
        )
        assert approved.data["approved"] is True
        after = await repository.identity_summary()
        assert after["latest_baseline"]["friend_count"] == 2
        assert after["friend_states"]["existing_friend"] == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_history_approval_rejects_unbounded_or_reversed_scope(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "history-scope.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=friend_list_transport())
    service = OwnerControlService(repository, client, owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    try:
        oversized = await service.execute(
            owner_command(
                OwnerCommandKind.USER_HISTORY_ONE_TIME,
                {"user_qq": "123456789", "max_messages": "501"},
            )
        )
        reversed_range = await service.execute(
            owner_command(
                OwnerCommandKind.USER_HISTORY_SELECTED_RANGE,
                {
                    "user_qq": "123456789",
                    "selected_from": "2026-08-10",
                    "selected_to": "2026-08-01",
                    "max_messages": "50",
                },
            )
        )

        assert oversized.status == "failed"
        assert oversized.data["message"] == "max_messages must be between 1 and 500"
        assert reversed_range.status == "failed"
        assert reversed_range.data["message"] == "selected_from must not be after selected_to"
        assert await repository.pending_approvals() == []
    finally:
        await client.close()


def history_pull_transport(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/get_friend_msg_history"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "retcode": 0,
                "data": {"messages": [{"id": "m1"}, {"id": "m2"}]},
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_owner_history_pull_returns_count_without_bodies(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "history-pull.sqlite3")
    await repository.initialize()
    await repository.set_history_policy(
        user_qq=OWNER_QQ,
        mode=HistoryAccessMode.ONE_TIME,
        max_messages=5,
        one_time_remaining=1,
        updated_by=OWNER_QQ,
        reason="approved:test",
    )
    calls: list[str] = []
    client = NapCatClient("http://napcat.test", transport=history_pull_transport(calls))
    reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    service = OwnerControlService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        history_reader=reader,
    )
    try:
        result = await service.execute(
            owner_command(OwnerCommandKind.USER_HISTORY_PULL, {"user_qq": OWNER_QQ})
        )
    finally:
        await client.close()

    assert result.status == "completed"
    assert result.data == {
        "allowed": True,
        "reason": "authorized",
        "user_qq": OWNER_QQ,
        "message_count": 2,
    }
    assert "messages" not in result.data
    assert calls == ["/get_friend_msg_history"]
    policy = (await repository.user_detail(OWNER_QQ))["history_policy"]
    assert policy["one_time_remaining"] == 0


@pytest.mark.asyncio
async def test_history_pull_rejects_other_users_without_network(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "history-pull-other.sqlite3")
    await repository.initialize()
    calls: list[str] = []
    client = NapCatClient("http://napcat.test", transport=history_pull_transport(calls))
    reader = NapCatHistoryReader(repository, client, readiness_guard=ALLOW_READINESS)
    service = OwnerControlService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        history_reader=reader,
    )
    try:
        result = await service.execute(
            owner_command(OwnerCommandKind.USER_HISTORY_PULL, {"user_qq": "123456789"})
        )
    finally:
        await client.close()

    assert result.status == "completed"
    assert result.data["allowed"] is False
    assert result.data["reason"] == "live_history_owner_only"
    assert calls == []
