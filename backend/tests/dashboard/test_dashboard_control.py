from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_and_owner_use_secured_control_api(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "dashboard.sqlite3",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/v1/control/commands",
            json={
                "action": "user.mark_existing_friend",
                "arguments": {"user_qq": "123456789"},
            },
        )
        assert unauthorized.status_code == 401

        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        identity = client.get("/api/v1/system/identity", headers=headers)
        assert identity.status_code == 200
        body = identity.json()
        assert body["brand"] == "YCH"
        assert body["creator_name"] == "维护者"
        assert body["locked"] is True
        assert body["accounts_editable"] is True
        assert body["owner_qq"] == "2000000001"
        assert any(bot["qq"] == "2000000002" for bot in body["bots"])
        protection = client.get("/api/v1/models/protection", headers=headers)
        assert protection.status_code == 200
        assert protection.json()["chat"]["state"] == "closed"
        assert protection.json()["chat"]["request_limit"] == 200
        assert protection.json()["image"]["request_limit"] == 20
        assert protection.json()["vision"]["request_limit"] == 40
        assert protection.json()["events"] == []
        image_task = client.post(
            "/api/v1/images/tasks",
            headers=headers,
            json={"prompt": "YCH local preview", "intended_use": "general"},
        )
        assert image_task.status_code == 200
        assert image_task.json()["status"] == "awaiting_model_config"
        task_id = image_task.json()["id"]
        assert (
            client.post(
                f"/api/v1/images/tasks/{task_id}/generate",
                headers=headers,
            ).status_code
            == 409
        )
        worker = client.get("/api/v1/knowledge/worker", headers=headers)
        assert worker.status_code == 200
        assert worker.json()["configured_enabled"] is False
        assert worker.json()["active"] is False
        run_once = client.post("/api/v1/knowledge/worker/run-once", headers=headers)
        assert run_once.json()["status"] == "disabled"
        worker_command = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "knowledge.worker_status", "arguments": {}},
        )
        assert worker_command.status_code == 200
        assert worker_command.json()["data"]["configured_enabled"] is False
        assert client.get("/api/v1/owner/reports", headers=headers).json()["items"] == []
        assert (
            client.put(
                "/api/v1/system/identity",
                headers=headers,
                json={"creator_name": "other"},
            ).status_code
            == 405
        )

        response = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={
                "action": "user.mark_existing_friend",
                "arguments": {"user_qq": "123456789"},
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        commands = client.get(
            "/api/v1/control/commands",
            headers=headers,
            params={"action": "user.mark_existing_friend"},
        )
        assert commands.status_code == 200
        assert commands.json()["items"][0]["result"]["friend_state"] == "existing_friend"

        detail = client.get("/api/v1/users/123456789", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["relationship"]["friend_state"] == "existing_friend"

        persona = client.post(
            "/api/v1/personas/manual",
            headers=headers,
            json={
                "scope": "private_user",
                "user_qq": "123456789",
                "name": "private style",
                "definition": "自然、简洁地交流",
            },
        )
        assert persona.status_code == 200
        preview = client.get(
            "/api/v1/personas/context-preview",
            headers=headers,
            params={"conversation_kind": "private", "peer_id": "123456789"},
        )
        assert preview.status_code == 200
        assert preview.json()["core_identity"]["creator_name"] == "维护者"
        assert preview.json()["private_definition"] == "自然、简洁地交流"

        group_preview = client.get(
            "/api/v1/personas/context-preview",
            headers=headers,
            params={"conversation_kind": "group", "peer_id": "123456789"},
        )
        assert group_preview.status_code == 200
        assert group_preview.json()["private_definition"] is None

        upload = client.post(
            "/api/v1/knowledge/documents",
            headers=headers,
            data={"user_qq": "123456789", "purpose": "user_understanding"},
            files={"document": ("notes.txt", "some notes", "text/plain")},
        )
        assert upload.status_code == 200
        assert upload.json()["status"] == "awaiting_model_config"
        jobs = client.get(
            "/api/v1/knowledge/jobs",
            headers=headers,
            params={"user_qq": "123456789"},
        )
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["purpose"] == "user_understanding"
        assert (
            client.post(
                f"/api/v1/knowledge/jobs/{upload.json()['job_id']}/process",
                headers=headers,
            ).status_code
            == 409
        )

        scheduled_for = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        proactive = client.post(
            "/api/v1/proactive/tasks",
            headers=headers,
            json={
                "target_qq": "123456789",
                "content": "稍后问候",
                "scheduled_for": scheduled_for,
                "timezone": "Asia/Shanghai",
            },
        )
        assert proactive.status_code == 200
        proactive_body = proactive.json()
        assert proactive_body["status"] == "pending_approval"
        task_id = proactive_body["id"]
        pending_approvals = client.get("/api/v1/control/approvals", headers=headers)
        assert pending_approvals.status_code == 200
        assert any(
            item["approval_code"] == proactive_body["approval_code"]
            for item in pending_approvals.json()["items"]
        )
        approval = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={
                "action": "approval.approve",
                "arguments": {"approval_code": proactive_body["approval_code"]},
            },
        )
        assert approval.json()["data"]["approved"] is True
        assert client.get("/api/v1/control/approvals", headers=headers).json()["items"] == []
        policy = client.get(
            "/api/v1/proactive/users/123456789/policy",
            headers=headers,
        ).json()
        assert policy["enabled"] is False
        policy["enabled"] = True
        policy.pop("persisted", None)
        policy.pop("updated_by", None)
        policy.pop("updated_at", None)
        updated_policy = client.put(
            "/api/v1/proactive/users/123456789/policy",
            headers=headers,
            json=policy,
        )
        assert updated_policy.status_code == 200
        assert updated_policy.json()["enabled"] is True
        calendar = client.get(
            "/api/v1/proactive/tasks",
            headers=headers,
            params={"target_qq": "123456789"},
        ).json()
        assert calendar["items"][0]["id"] == task_id
        summary = client.get("/api/v1/proactive/summary", headers=headers).json()
        assert summary["uses_real_calendar_time"] is True
        assert summary["outbound_enabled"] is False
        assert summary["scheduler"]["configured_enabled"] is False
        assert (
            client.post("/api/v1/proactive/worker/run-once", headers=headers).json()["status"]
            == "disabled"
        )
        cancelled = client.post(
            f"/api/v1/proactive/tasks/{task_id}/cancel",
            headers=headers,
        )
        assert cancelled.json()["status"] == "cancelled"
        assert client.get("/api/v1/status").json()["storage"]["outbox"] == 0

        assert client.delete("/api/v1/auth/session", headers=headers).json()["revoked"] is True
        assert client.get("/api/v1/users/123456789", headers=headers).status_code == 401


def test_url_image_route_cannot_execute_local_artifact_tasks(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "url-image.sqlite3",
        admin_access_token="dashboard-token",
        model_network_enabled=True,
        image_model_enabled=True,
        image_api_protocol="openai_compatible",
        image_api_base="https://image.invalid/v1",
        image_model="url-only-model",
        image_response_format="url",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        status = client.get("/api/v1/status").json()["models"]["image"]
        assert status["active"] is True
        assert status["task_execution_enabled"] is False
        assert status["local_artifact_ingestion"] == "remote_url_rejected"
        task = client.post(
            "/api/v1/images/tasks",
            headers=headers,
            json={"prompt": "must remain local", "intended_use": "general"},
        ).json()
        assert task["status"] == "awaiting_model_config"
