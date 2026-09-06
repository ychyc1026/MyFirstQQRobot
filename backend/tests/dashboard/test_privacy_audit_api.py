import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_privacy_access_log_is_authenticated_filterable_and_redacted(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=tmp_path / "privacy-audit.sqlite3",
            admin_access_token="dashboard-token",
        )
    )
    with TestClient(app) as client:
        asyncio.run(
            app.state.repository.authorize_history_access(
                user_qq="123456789",
                requested_count=20,
                include_media=False,
                purpose="initialize_user_profile",
                accessor="napcat.get_friend_msg_history",
                source_supports_range=False,
            )
        )
        assert client.get("/api/v1/privacy/access-log").status_code == 401
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/privacy/access-log",
            params={"user_qq": "123456789", "decision": "denied"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {
            "total": 1,
            "allowed": 0,
            "denied": 1,
            "data_classes": {"message_content": 1},
        }
        assert body["items"][0]["policy"]["reason"] == "history_mode_deny"
        assert "policy_snapshot_json" not in body["items"][0]
        assert "plain_text" not in str(body)
        assert (
            client.get(
                "/api/v1/privacy/access-log",
                params={"user_qq": "not-a-qq"},
                headers=headers,
            ).status_code
            == 422
        )
