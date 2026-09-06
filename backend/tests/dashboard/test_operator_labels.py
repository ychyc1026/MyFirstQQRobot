from pathlib import Path

import pytest
from _support.owner import (
    BOT_QQ,
    OWNER_QQ,
    friend_list_transport,
    owner_command,
    parse_owner_text,
)
from _support.paths import SOURCE_ROOT
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import OwnerControlService
from ych_bot.config import Settings
from ych_bot.domain.control import OwnerCommandKind, parse_owner_command
from ych_bot.domain.models import ConversationKind
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient
from ych_bot.infrastructure.napcat.parser import parse_message_event


def test_owner_can_set_and_clear_user_label_commands() -> None:
    named = parse_owner_text("/用户 2000000003 备注 小明")
    cleared = parse_owner_text("/用户 2000000003 备注 清除")
    lookup = parse_owner_text("/用户 2000000003 备注")
    group = parse_owner_text("/群 2000000004 备注 测试群")

    assert named is not None
    assert named.kind is OwnerCommandKind.USER_SET_LABEL
    assert named.arguments == {"user_qq": "2000000003", "label": "小明"}
    assert cleared is not None
    assert cleared.arguments == {"user_qq": "2000000003", "clear": "true"}
    assert lookup is not None
    assert "label" not in lookup.arguments
    assert group is not None
    assert group.kind is OwnerCommandKind.GROUP_SET_LABEL
    assert group.arguments == {"group_qq": "2000000004", "label": "测试群"}


@pytest.mark.asyncio
async def test_operator_label_is_persisted_and_listed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "labels.sqlite3")
    await repository.initialize()
    client = NapCatClient("http://napcat.test", transport=friend_list_transport())
    service = OwnerControlService(repository, client, owner_qq=OWNER_QQ, bot_qq=BOT_QQ)
    try:
        await service.execute(
            owner_command(OwnerCommandKind.USER_MARK_EXISTING_FRIEND, {"user_qq": "2000000003"})
        )
        saved = await service.execute(
            owner_command(
                OwnerCommandKind.USER_SET_LABEL, {"user_qq": "2000000003", "label": "小明"}
            )
        )
        assert saved.status == "completed"
        assert saved.data["label"] == "小明"
        detail = await repository.user_detail("2000000003")
        assert detail["operator_label"] == "小明"
        listed = await repository.list_known_users()
        assert any(
            item["user_qq"] == "2000000003" and item["operator_label"] == "小明" for item in listed
        )
        cleared = await service.execute(
            owner_command(OwnerCommandKind.USER_SET_LABEL, {"user_qq": "2000000003", "label": ""})
        )
        assert cleared.data["label"] == ""
        assert (await repository.user_detail("2000000003"))["operator_label"] == ""
    finally:
        await client.close()


def test_dashboard_can_list_and_save_operator_labels(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "labels-ui.sqlite3",
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
        saved = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={
                "action": "user.set_label",
                "arguments": {"user_qq": "30003", "label": "小明"},
            },
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["label"] == "小明"
        labels = client.get("/api/v1/operator-labels", headers=headers)
        assert labels.json()["items"][0]["label"] == "小明"
        detail = client.get("/api/v1/users/30003", headers=headers)
        assert detail.json()["operator_label"] == "小明"


def test_reply_context_code_does_not_load_operator_labels() -> None:
    application = SOURCE_ROOT / "application"
    roots = [
        application / "context_assembly.py",
        application / "context.py",
        application / "reply_planning.py",
        application / "reply_worker.py",
    ]
    for path in roots:
        text = path.read_text(encoding="utf-8")
        assert "operator_label" not in text
        assert "operator_labels" not in text


def test_group_message_cannot_set_operator_label() -> None:
    payload = {
        "time": 1_700_000_000,
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "group",
        "message_id": 910,
        "user_id": int(OWNER_QQ),
        "group_id": 2000000004,
        "message": [{"type": "text", "data": {"text": "/用户 2000000003 备注 小明"}}],
    }
    message = parse_message_event(payload, expected_bot_qq=BOT_QQ)
    assert parse_owner_command(message, owner_qq=OWNER_QQ) is None
    assert message.conversation_kind is ConversationKind.GROUP
