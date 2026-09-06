from __future__ import annotations

from pathlib import Path
from time import sleep

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from ych_bot.api.app import create_app
from ych_bot.config import Settings

BOT_QQ = "2000000002"


def _settings(tmp_path: Path, *, token: str = "observe-token") -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / "observe-onebot.sqlite3",
        onebot_access_token=token,
        admin_access_token="dashboard-token",
        outbound_enabled=False,
        reply_worker_enabled=False,
        reply_runtime_max_mode="observe_only",
        ingest_enabled=True,
    )


def _private_event(*, message_id: int = 9001, self_id: int = 2000000002) -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": self_id,
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": 123456789,
        "message": [{"type": "text", "data": {"text": "observe ping"}}],
    }


def _heartbeat(*, self_id: int = 2000000002) -> dict:
    return {
        "time": 1_700_000_050,
        "self_id": self_id,
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "status": {"online": True, "good": True},
        "interval": 5000,
    }


def _wait_status(client: TestClient, predicate) -> dict:
    status = {}
    for _ in range(100):
        status = client.get("/api/v1/status").json()
        if predicate(status):
            return status
        sleep(0.01)
    return status


def test_onebot_websocket_rejects_empty_token(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, token=""))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/onebot/v11/ws"):
            pass
        status = client.get("/api/v1/status").json()
        assert status["onebot"]["connected"] is False
        assert status["onebot"]["auth_configured"] is False
        assert status["storage"]["messages"] == 0


def test_onebot_websocket_rejects_mismatched_token(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:  # noqa: SIM117
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/onebot/v11/ws",
                headers={"Authorization": "Bearer wrong-token"},
            ),
        ):
            pass


def test_observe_ingest_stores_exact_bot_without_napcat_http_or_outbox(
    tmp_path: Path,
) -> None:
    def forbidden_factory(base_url: str, access_token: str):
        del base_url, access_token
        raise AssertionError("NapCat HTTP client must not be constructed during observe ingest")

    app = create_app(_settings(tmp_path), napcat_factory=forbidden_factory)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer observe-token"},
        ) as websocket:
            websocket.send_json(_heartbeat())
            websocket.send_json(_private_event())
            websocket.send_json(_private_event())
            websocket.send_json(_private_event(message_id=9002, self_id=111111111))
            status = _wait_status(
                client,
                lambda item: (
                    item["onebot"]["stored_events"] == 1
                    and item["onebot"]["duplicate_events"] == 1
                    and item["storage"]["messages"] == 1
                ),
            )

        assert status["mode"] == "observe_only"
        assert status["onebot"]["auth_configured"] is True
        assert status["onebot"]["authenticated_bot_qq"] == BOT_QQ
        assert status["onebot"]["exact_bot"] is True
        assert status["onebot"]["ignored_events"] == 1
        assert status["storage"]["messages"] == 1
        assert status["storage"]["outbox"] == 0
        assert status["proactive_messages"]["outbound_enabled"] is False
        assert app.state.napcat_client.constructed is False
        assert app.state.reply_runtime_worker.configured_enabled is False


def test_heartbeat_does_not_count_as_ignored_conversation(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer observe-token"},
        ) as websocket:
            websocket.send_json(_heartbeat())
            status = _wait_status(
                client,
                lambda item: item["onebot"]["authenticated_bot_qq"] == BOT_QQ,
            )

        assert status["onebot"]["ignored_events"] == 0
        assert status["onebot"]["stored_events"] == 0
        assert status["onebot"]["exact_bot"] is True
        assert status["storage"]["messages"] == 0
