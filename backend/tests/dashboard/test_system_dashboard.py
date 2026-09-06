from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_system_page_shows_locked_identity_and_closed_switches(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "system-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        outbound_enabled=False,
        shadow_inference_enabled=False,
        model_network_enabled=False,
        qzone_publish_enabled=False,
        privacy_jobs_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        identity = client.get("/api/v1/system/identity", headers=headers)
        assert identity.status_code == 200
        assert identity.json()["brand"] == "YCH"
        assert identity.json()["creator_name"] == "维护者"
        assert identity.json()["locked"] is True
        privacy = client.get("/api/v1/privacy/status", headers=headers)
        assert privacy.json()["live_history_read_enabled"] is False
        assert privacy.json()["shadow_inference_enabled"] is False
        assert privacy.json()["privacy_jobs_enabled"] is False
        status = client.get("/api/v1/status").json()
        assert status["mode"] == "observe_only"
        assert status["control"]["qzone_publish_enabled"] is False
        assert client.get("/api/v1/control/commands", headers=headers).json()["items"] == []
        assert client.get("/api/v1/privacy/requests", headers=headers).json()["items"] == []
        pause = client.post("/api/v1/outbox/worker/pause", headers=headers)
        assert pause.status_code == 200
        assert pause.json()["changed"] is False
        outbound = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "outbound.pause", "arguments": {}},
        )
        assert outbound.status_code == 200
        commands = client.get("/api/v1/control/commands", headers=headers)
        assert any(item["action"] == "outbound.pause" for item in commands.json()["items"])
