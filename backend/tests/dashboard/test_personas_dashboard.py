from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_personas_page_can_save_and_preview_isolation(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "personas-ui.sqlite3",
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
        assert client.get("/api/v1/personas", headers=headers).json()["items"] == []
        global_persona = client.post(
            "/api/v1/personas/manual",
            headers=headers,
            json={"scope": "global", "name": "base", "definition": "稳重、简洁"},
        )
        assert global_persona.status_code == 200
        private = client.post(
            "/api/v1/personas/manual",
            headers=headers,
            json={
                "scope": "private_user",
                "user_qq": "30003",
                "name": "private",
                "definition": "更自然地交流",
            },
        )
        assert private.status_code == 200
        listed = client.get("/api/v1/personas", headers=headers).json()["items"]
        assert len(listed) == 2
        assert any(item["scope_type"] == "global" for item in listed)
        private_preview = client.get(
            "/api/v1/personas/context-preview",
            headers=headers,
            params={"conversation_kind": "private", "peer_id": "30003"},
        )
        assert private_preview.status_code == 200
        body = private_preview.json()
        assert body["core_identity"]["creator_name"] == "维护者"
        assert body["base_definition"] == "稳重、简洁"
        assert body["private_definition"] == "更自然地交流"
        group_preview = client.get(
            "/api/v1/personas/context-preview",
            headers=headers,
            params={"conversation_kind": "group", "peer_id": "30003"},
        )
        assert group_preview.json()["private_definition"] is None
        assert group_preview.json()["user_context"] is None
