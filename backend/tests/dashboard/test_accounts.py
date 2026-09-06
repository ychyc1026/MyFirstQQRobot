from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application.accounts import AccountService
from ych_bot.application.ingestion import MessageIngestionService
from ych_bot.config import Settings
from ych_bot.infrastructure.database import SQLiteRepository


def private_event(*, self_id: int, user_id: int = 10001, message_id: int = 1) -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": self_id,
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }


def test_settings_allow_custom_owner_and_bot_qq(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path, owner_qq="10001", bot_qq="20002")
    assert settings.owner_qq == "10001"
    assert settings.bot_qq == "20002"


@pytest.mark.asyncio
async def test_account_service_seeds_adds_and_disables_bots(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "accounts.sqlite3")
    await repository.initialize()
    accounts = AccountService(repository)
    await accounts.ensure_seeded(owner_qq="10001", bot_qq="20002")

    snapshot = await accounts.snapshot()
    assert snapshot["owner_qq"] == "10001"
    assert snapshot["bots"][0]["qq"] == "20002"
    assert snapshot["bots"][0]["enabled"] is True

    await accounts.add_bot("20003", label="二号", updated_by="10001")
    await accounts.set_owner("10009", updated_by="10001")
    await accounts.disable_bot("20002", updated_by="10009")

    snapshot = await accounts.snapshot()
    assert snapshot["owner_qq"] == "10009"
    enabled = {bot["qq"] for bot in snapshot["bots"] if bot["enabled"]}
    assert enabled == {"20003"}
    assert await repository.enabled_bot_qqs() == {"20003"}


@pytest.mark.asyncio
async def test_ingest_accepts_enabled_bots_only(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "ingest-bots.sqlite3")
    await repository.initialize()
    accounts = AccountService(repository)
    await accounts.ensure_seeded(owner_qq="10001", bot_qq="20002")
    await accounts.add_bot("20003", label="二号", updated_by="10001")
    ingestion = MessageIngestionService(repository, bot_qq="20002", enabled=True, owner_qq="10001")

    first = await ingestion.ingest(private_event(self_id=20003, message_id=11))
    unknown = await ingestion.ingest(private_event(self_id=99999, message_id=12))
    assert first.status == "stored"
    assert unknown.status == "ignored"
    assert unknown.reason == "foreign_bot_event"


def test_accounts_api_can_add_bot_and_change_owner(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "accounts-api.sqlite3",
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
        snapshot = client.get("/api/v1/accounts", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.json()["owner_qq"] == "10001"
        created = client.post(
            "/api/v1/accounts/bots",
            headers=headers,
            json={"qq": "20003", "label": "二号"},
        )
        assert created.status_code == 200
        updated = client.put(
            "/api/v1/accounts/owner",
            headers=headers,
            json={"owner_qq": "10009"},
        )
        assert updated.status_code == 200
        assert updated.json()["owner_qq"] == "10009"
