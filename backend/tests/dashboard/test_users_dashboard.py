from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_users_page_can_list_mark_freeze_and_request_privacy(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "users-ui.sqlite3",
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
        assert client.get("/api/v1/users", headers=headers).json()["items"] == []
        marked = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={
                "action": "user.mark_existing_friend",
                "arguments": {"user_qq": "30003"},
            },
        )
        assert marked.status_code == 200
        listed = client.get("/api/v1/users", headers=headers)
        assert listed.status_code == 200
        assert any(item["user_qq"] == "30003" for item in listed.json()["items"])
        assert listed.json()["summary"]["friend_states"]["existing_friend"] == 1
        detail = client.get("/api/v1/users/30003", headers=headers)
        assert detail.json()["relationship"]["friend_state"] == "existing_friend"
        assert detail.json()["history_policy"]["mode"] == "deny"
        assert detail.json()["reply_style"]["min_bubbles"] == 1
        assert detail.json()["reply_style"]["max_bubbles"] == 1
        frozen = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "privacy.freeze", "arguments": {"user_qq": "30003"}},
        )
        assert frozen.json()["data"]["frozen"] is True
        assert client.get("/api/v1/users/30003", headers=headers).json()["relationship"][
            "data_frozen"
        ]
        history = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "user.history_file_only", "arguments": {"user_qq": "30003"}},
        )
        assert history.json()["data"]["history_mode"] == "file_import_only"
        export = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "privacy.export", "arguments": {"user_qq": "30003"}},
        )
        assert export.json()["status"] == "pending_approval"
        assert export.json()["data"]["approval_code"]
