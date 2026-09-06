from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_qzone_page_can_draft_request_and_see_disabled_worker(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "qzone-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        qzone_publish_enabled=False,
        qzone_worker_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        summary = client.get("/api/v1/qzone/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["publisher"]["active"] is False
        assert summary.json()["automatic_network_retry"] is False
        assert summary.json()["uses_real_calendar_time"] is True
        assert client.get("/api/v1/qzone/posts", headers=headers).json()["items"] == []
        draft = client.post(
            "/api/v1/qzone/drafts",
            headers=headers,
            json={"content": "仪表盘草稿", "visibility": 4},
        )
        assert draft.status_code == 200
        assert draft.json()["status"] == "draft"
        draft_id = draft.json()["id"]
        invalid = client.post(
            "/api/v1/qzone/posts",
            headers=headers,
            json={"content": "指定可见但没有目标", "visibility": 16},
        )
        assert invalid.status_code == 422
        publish = client.post(
            "/api/v1/qzone/posts",
            headers=headers,
            json={"content": "仪表盘待审批空间", "visibility": 4},
        )
        assert publish.status_code == 200
        assert publish.json()["status"] == "pending_approval"
        assert publish.json()["approval_code"]
        detail = client.get(f"/api/v1/qzone/posts/{publish.json()['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["content"] == "仪表盘待审批空间"
        cancelled = client.post(
            f"/api/v1/qzone/posts/{draft_id}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        revoke_draft = client.post(
            f"/api/v1/qzone/posts/{draft_id}/revoke",
            headers=headers,
        )
        assert revoke_draft.status_code == 409
        worker = client.get("/api/v1/qzone/worker", headers=headers)
        assert worker.json()["configured_enabled"] is False
        assert worker.json()["publish_route_enabled"] is False
        run_once = client.post("/api/v1/qzone/worker/run-once", headers=headers)
        assert run_once.json()["status"] == "disabled"
        policy = client.get("/api/v1/qzone/policy", headers=headers)
        assert policy.status_code == 200
        assert policy.json()["quiet_hours_enabled"] is True
        listed = client.get("/api/v1/qzone/posts", headers=headers).json()["items"]
        assert {item["status"] for item in listed} == {"cancelled", "pending_approval"}
