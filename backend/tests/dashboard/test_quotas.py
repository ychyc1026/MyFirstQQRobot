from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application.accounts import AccountService
from ych_bot.application.quotas import (
    DEFAULT_GROUP_DAILY_TOKENS,
    DEFAULT_PRIVATE_DAILY_TOKENS,
    ChatQuotaService,
)
from ych_bot.config import Settings
from ych_bot.domain.models import ConversationKind, MessageDirection, MessageSegment, UnifiedMessage
from ych_bot.infrastructure.database import SQLiteRepository


def _message(
    *,
    bot_qq: str,
    kind: ConversationKind,
    peer_id: str,
    sender_id: str,
) -> UnifiedMessage:
    return UnifiedMessage(
        id=str(uuid4()),
        event_key=f"test:{bot_qq}:{peer_id}",
        source_message_id="1",
        bot_qq=bot_qq,
        direction=MessageDirection.INBOUND,
        conversation_kind=kind,
        conversation_id=peer_id,
        sender_id=sender_id,
        occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        segments=(MessageSegment(type="text", data={"text": "hi"}),),
        raw_event={},
    )


async def _record_usage(
    repository: SQLiteRepository,
    *,
    bot_qq: str,
    actor_qq: str,
    conversation_key: str,
    tokens: int,
) -> None:
    run_id = str(uuid4())
    await repository.start_inference_run(
        run_id=run_id,
        source_message_id=str(uuid4()),
        conversation_key=conversation_key,
        actor_qq=actor_qq,
        mode="shadow",
        prompt_hash="hash",
        model_route="test",
        bot_qq=bot_qq,
    )
    await repository.finish_inference_success(
        run_id=run_id,
        candidate_id=str(uuid4()),
        source_message_id="src",
        conversation_kind="group" if ":group:" in conversation_key else "private",
        target_id=actor_qq,
        content="ok",
        provider_request_id="p",
        input_tokens=tokens,
        output_tokens=0,
        safety_flags=[],
    )


@pytest.mark.asyncio
async def test_private_quota_notifies_owner_once_and_replies_user(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "quota-private.sqlite3")
    await repository.initialize()
    accounts = AccountService(repository)
    await accounts.ensure_seeded(owner_qq="10001", bot_qq="20002")
    quotas = ChatQuotaService(repository, timezone="Asia/Shanghai")
    message = _message(
        bot_qq="20002",
        kind=ConversationKind.PRIVATE,
        peer_id="30003",
        sender_id="30003",
    )
    await _record_usage(
        repository,
        bot_qq="20002",
        actor_qq="30003",
        conversation_key=message.conversation_key,
        tokens=DEFAULT_PRIVATE_DAILY_TOKENS,
    )

    first = await quotas.evaluate(message)
    assert first.exceeded is True
    handled = await quotas.handle_exceeded(message, first)
    assert handled["owner_notified"] is True
    assert handled["user_replied"] is True
    reports = await repository.owner_reports(status="pending", limit=20)
    assert any("30003" in item["body"] for item in reports)
    assert (await repository.counts())["outbox"] == 1

    second = await quotas.handle_exceeded(message, first)
    assert second["owner_notified"] is False
    assert second["user_replied"] is False
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_group_quota_is_silent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "quota-group.sqlite3")
    await repository.initialize()
    accounts = AccountService(repository)
    await accounts.ensure_seeded(owner_qq="10001", bot_qq="20002")
    quotas = ChatQuotaService(repository, timezone="Asia/Shanghai")
    message = _message(
        bot_qq="20002",
        kind=ConversationKind.GROUP,
        peer_id="888",
        sender_id="30003",
    )
    await _record_usage(
        repository,
        bot_qq="20002",
        actor_qq="30003",
        conversation_key=message.conversation_key,
        tokens=DEFAULT_GROUP_DAILY_TOKENS,
    )
    decision = await quotas.evaluate(message)
    assert decision.exceeded is True
    handled = await quotas.handle_exceeded(message, decision)
    assert handled["owner_notified"] is False
    assert handled["user_replied"] is False
    assert await repository.owner_reports(status="pending", limit=20) == []
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_today_bonus_allows_private_chat_again(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "quota-bonus.sqlite3")
    await repository.initialize()
    accounts = AccountService(repository)
    await accounts.ensure_seeded(owner_qq="10001", bot_qq="20002")
    quotas = ChatQuotaService(repository, timezone="Asia/Shanghai")
    message = _message(
        bot_qq="20002",
        kind=ConversationKind.PRIVATE,
        peer_id="30003",
        sender_id="30003",
    )
    await _record_usage(
        repository,
        bot_qq="20002",
        actor_qq="30003",
        conversation_key=message.conversation_key,
        tokens=DEFAULT_PRIVATE_DAILY_TOKENS,
    )
    assert (await quotas.evaluate(message)).exceeded is True
    await quotas.add_today_bonus(
        bot_qq="20002",
        peer_kind="private",
        peer_id="30003",
        amount=1_000,
        updated_by="10001",
    )
    after = await quotas.evaluate(message)
    assert after.exceeded is False
    assert after.today_bonus == 1_000


def test_quotas_api_can_set_private_and_group_limits(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "quotas-api.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        private_limit = client.put(
            "/api/v1/quotas/20002/private/30003",
            headers=headers,
            json={"daily_limit": 1000, "display_name": "小明"},
        )
        assert private_limit.status_code == 200
        assert private_limit.json()["daily_limit"] == 1000
        group_limit = client.put(
            "/api/v1/quotas/20002/group/888",
            headers=headers,
            json={"daily_limit": 3_000_000},
        )
        assert group_limit.status_code == 200
        bonus = client.post(
            "/api/v1/quotas/20002/private/30003/today-bonus",
            headers=headers,
            json={"amount": 200},
        )
        assert bonus.status_code == 200
        assert bonus.json()["today_bonus"] == 200
        listed = client.get(
            "/api/v1/quotas?bot_qq=20002&peer_kind=private",
            headers=headers,
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["peer_id"] == "30003"
        assert listed.json()["items"][0]["display_name"] == "小明"
