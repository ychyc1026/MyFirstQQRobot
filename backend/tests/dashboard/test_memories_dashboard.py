from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_memories_page_can_write_conflict_preview_and_forget(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "memories-ui.sqlite3",
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
        user_qq = "30003"
        assert (
            client.get(f"/api/v1/users/{user_qq}/memories", headers=headers).json()["items"] == []
        )
        first = client.post(
            f"/api/v1/users/{user_qq}/memories/manual",
            headers=headers,
            json={"kind": "preference", "key": "favorite_drink", "value": {"name": "tea"}},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "active"
        duplicate = client.post(
            f"/api/v1/users/{user_qq}/memories/manual",
            headers=headers,
            json={"kind": "preference", "key": "favorite_drink", "value": {"name": "tea"}},
        )
        assert duplicate.json()["deduplicated"] is True
        conflict = client.post(
            f"/api/v1/users/{user_qq}/memories/manual",
            headers=headers,
            json={"kind": "preference", "key": "favorite_drink", "value": {"name": "coffee"}},
        )
        assert conflict.status_code == 200
        assert conflict.json()["status"] == "disputed"
        conflict_id = conflict.json()["conflict_id"]
        assert conflict_id
        pending = client.get("/api/v1/memory/conflicts", headers=headers)
        assert any(item["id"] == conflict_id for item in pending.json()["items"])
        private_while_disputed = client.get(
            f"/api/v1/users/{user_qq}/memory-context-preview",
            headers=headers,
            params={"conversation_kind": "private"},
        )
        assert private_while_disputed.json()["items"] == []
        group_preview = client.get(
            f"/api/v1/users/{user_qq}/memory-context-preview",
            headers=headers,
            params={"conversation_kind": "group"},
        )
        assert group_preview.json()["items"] == []
        resolved = client.post(
            f"/api/v1/memory/conflicts/{conflict_id}/resolve",
            headers=headers,
            json={"resolution": "keep_right"},
        )
        assert resolved.json()["resolved"] is True
        private_preview = client.get(
            f"/api/v1/users/{user_qq}/memory-context-preview",
            headers=headers,
            params={"conversation_kind": "private"},
        )
        assert private_preview.json()["items"][0]["value"] == {"name": "coffee"}
        assert (
            client.get(
                f"/api/v1/users/{user_qq}/memory-context-preview",
                headers=headers,
                params={"conversation_kind": "group"},
            ).json()["items"]
            == []
        )
        memory_id = private_preview.json()["items"][0]["id"]
        forgotten = client.delete(
            f"/api/v1/users/{user_qq}/memories/{memory_id}",
            headers=headers,
        )
        assert forgotten.json()["forgotten"] is True
        assert (
            client.get(
                f"/api/v1/users/{user_qq}/memory-context-preview",
                headers=headers,
                params={"conversation_kind": "private"},
            ).json()["items"]
            == []
        )
        assert client.get("/api/v1/memory/conflicts", headers=headers).json()["items"] == []
