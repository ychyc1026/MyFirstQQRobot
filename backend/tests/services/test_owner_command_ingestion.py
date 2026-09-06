from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_owner_private_command_flows_through_shared_control_service(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "owner-command.sqlite3",
        onebot_access_token="onebot-token",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)
    event = {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 4001,
        "user_id": 2000000001,
        "message": [{"type": "text", "data": {"text": "/用户 123456789 标记旧友"}}],
    }

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer onebot-token"},
        ) as websocket:
            websocket.send_json(event)

            for _ in range(100):
                detail = client.get("/api/v1/users/123456789", headers=admin_headers).json()
                if detail["relationship"] is not None:
                    break
                sleep(0.01)

        assert detail["relationship"]["friend_state"] == "existing_friend"
        assert detail["relationship"]["state_source"] == "owner_override"
