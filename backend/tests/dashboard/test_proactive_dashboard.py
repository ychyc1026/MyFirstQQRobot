from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_proactive_page_can_create_policy_and_cancel(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "proactive-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        outbound_enabled=False,
        proactive_scheduler_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        summary = client.get("/api/v1/proactive/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["outbound_enabled"] is False
        assert summary.json()["scheduler"]["configured_enabled"] is False
        assert summary.json()["uses_real_calendar_time"] is True
        scheduled_for = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        created = client.post(
            "/api/v1/proactive/tasks",
            headers=headers,
            json={
                "target_qq": "30003",
                "content": "稍后问候",
                "scheduled_for": scheduled_for,
                "timezone": "Asia/Shanghai",
            },
        )
        assert created.status_code == 200
        assert created.json()["status"] == "pending_approval"
        assert created.json()["approval_code"]
        task_id = created.json()["id"]
        policy = client.get("/api/v1/proactive/users/30003/policy", headers=headers)
        assert policy.status_code == 200
        assert policy.json()["enabled"] is False
        enabled = client.put(
            "/api/v1/proactive/users/30003/policy",
            headers=headers,
            json={
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "quiet_hours_enabled": True,
                "quiet_start": "22:00",
                "quiet_end": "08:00",
                "quiet_behavior": "delay",
                "daily_limit": 3,
                "minimum_interval_seconds": 3600,
            },
        )
        assert enabled.json()["enabled"] is True
        listed = client.get(
            "/api/v1/proactive/tasks",
            headers=headers,
            params={"target_qq": "30003"},
        )
        assert any(item["id"] == task_id for item in listed.json()["items"])
        detail = client.get(f"/api/v1/proactive/tasks/{task_id}", headers=headers)
        assert detail.json()["content"] == "稍后问候"
        cancelled = client.post(
            f"/api/v1/proactive/tasks/{task_id}/cancel",
            headers=headers,
        )
        assert cancelled.json()["status"] == "cancelled"
        run_once = client.post("/api/v1/proactive/worker/run-once", headers=headers)
        assert run_once.json()["status"] == "disabled"
        outbox = client.get("/api/v1/outbox/worker", headers=headers)
        assert outbox.json()["configured_enabled"] is False
