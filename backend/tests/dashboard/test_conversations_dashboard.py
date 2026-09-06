from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_conversations_page_can_preview_isolation_and_list_shadow(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "conversations-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        shadow_inference_enabled=False,
        model_network_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/v1/inference/runs", headers=headers).json()["items"] == []
        assert client.get("/api/v1/reply-candidates", headers=headers).json()["items"] == []
        assert (
            client.get("/api/v1/privacy/status", headers=headers).json()["shadow_inference_enabled"]
            is False
        )
        client.post(
            "/api/v1/personas/manual",
            headers=headers,
            json={"scope": "global", "name": "base", "definition": "稳重、简洁"},
        )
        client.post(
            "/api/v1/personas/manual",
            headers=headers,
            json={
                "scope": "private_user",
                "user_qq": "30003",
                "name": "private",
                "definition": "更自然地交流",
            },
        )
        client.post(
            "/api/v1/users/30003/memories/manual",
            headers=headers,
            json={"kind": "fact", "key": "city", "value": {"name": "Shanghai"}},
        )
        private = client.get(
            "/api/v1/conversations/context-preview",
            headers=headers,
            params={
                "conversation_kind": "private",
                "peer_id": "30003",
                "actor_qq": "30003",
            },
        )
        assert private.status_code == 200
        body = private.json()
        assert body["core_identity"]["brand"] == "YCH"
        assert body["core_identity"]["creator_name"] == "维护者"
        assert body["authenticated_creator"] is False
        assert body["persona"]["base_definition"] == "稳重、简洁"
        assert body["persona"]["private_definition"] == "更自然地交流"
        assert body["isolation"]["private_user_layers_allowed"] is True
        assert body["isolation"]["memory_is_instruction"] is False
        assert len(body["memories"]) == 1
        owner = client.get(
            "/api/v1/conversations/context-preview",
            headers=headers,
            params={
                "conversation_kind": "private",
                "peer_id": "2000000001",
                "actor_qq": "2000000001",
            },
        )
        assert owner.json()["authenticated_creator"] is True
        group = client.get(
            "/api/v1/conversations/context-preview",
            headers=headers,
            params={
                "conversation_kind": "group",
                "peer_id": "30003",
                "actor_qq": "30003",
            },
        )
        assert group.json()["persona"]["private_definition"] is None
        assert group.json()["user_reference"] is None
        assert group.json()["memories"] == []
        assert group.json()["isolation"]["private_user_layers_allowed"] is False
        replay = client.post(
            "/api/v1/inference/replay",
            headers=headers,
            json={"message_id": "missing"},
        )
        assert replay.status_code == 422
        assert "disabled" in replay.json()["detail"]
