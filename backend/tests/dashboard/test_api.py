from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def event() -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 303,
        "user_id": 123456789,
        "message": [{"type": "text", "data": {"text": "test"}}],
    }


def friend_add_event() -> dict:
    return {
        "time": 1_700_000_100,
        "self_id": 2000000002,
        "post_type": "notice",
        "notice_type": "friend_add",
        "user_id": 987654321,
    }


def test_health_and_websocket_ingestion(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "api.sqlite3",
        onebot_access_token="test-token",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        assert client.get("/health/ready").json()["status"] == "ready"

        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer test-token"},
        ) as websocket:
            websocket.send_json(event())
            websocket.send_json(event())
            websocket.send_json(friend_add_event())

            for _ in range(100):
                live_status = client.get("/api/v1/status").json()
                if (
                    live_status["onebot"]["stored_events"] == 2
                    and live_status["onebot"]["duplicate_events"] == 1
                ):
                    break
                sleep(0.01)

        status = client.get("/api/v1/status").json()
        assert status["bot_qq"] == "2000000002"
        assert status["owner_qq"] == "2000000001"
        assert status["mode"] == "observe_only"
        assert status["control"]["qzone_publish_enabled"] is False
        assert status["models"]["chat"]["protection"]["state"] == "closed"
        assert status["models"]["chat"]["protection"]["request_limit"] == 200
        assert status["models"]["chat"]["protection"]["token_limit"] == 500_000
        assert status["models"]["image"]["protection"]["request_limit"] == 20
        assert status["models"]["image"]["protection"]["token_limit"] is None
        assert status["models"]["vision"]["protection"]["request_limit"] == 40
        assert status["models"]["vision"]["route_enabled"] is False
        assert status["storage"]["messages"] == 1
        assert status["onebot"]["stored_events"] == 2
        assert status["onebot"]["duplicate_events"] == 1

        assert client.get("/api/v1/users/summary").status_code == 401
        assert client.get("/api/v1/privacy/status").status_code == 401
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        users = client.get("/api/v1/users/summary", headers=headers).json()
        assert users["classification_rule"] == "evidence_based"
        assert users["default_history_access"] == "deny"
        assert users["friend_states"]["unknown"] == 1
        assert users["history_modes"]["deny"] == 1

        privacy = client.get("/api/v1/privacy/status", headers=headers).json()
        assert privacy["live_history_read_enabled"] is False
        assert privacy["qzone_profile_collection_enabled"] is False
        assert privacy["document_ocr_enabled"] is False
        assert privacy["image_content_review_enabled"] is False
        assert privacy["image_orphan_scan_enabled"] is False
        assert privacy["shadow_inference_enabled"] is False
