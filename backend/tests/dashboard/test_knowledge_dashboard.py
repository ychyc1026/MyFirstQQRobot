from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings


def test_dashboard_knowledge_page_can_upload_list_and_see_disabled_worker(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "knowledge-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        knowledge_processing_enabled=False,
        knowledge_worker_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/v1/knowledge/jobs", headers=headers).json()["items"] == []
        upload = client.post(
            "/api/v1/knowledge/documents",
            headers=headers,
            data={"user_qq": "30003", "purpose": "user_understanding"},
            files={"document": ("notes.txt", "喜欢喝茶，说话简洁。", "text/plain")},
        )
        assert upload.status_code == 200
        body = upload.json()
        assert body["status"] == "awaiting_model_config"
        job_id = body["job_id"]
        listed = client.get(
            "/api/v1/knowledge/jobs",
            headers=headers,
            params={"user_qq": "30003"},
        )
        assert listed.status_code == 200
        assert any(item["id"] == job_id for item in listed.json()["items"])
        detail = client.get(f"/api/v1/knowledge/jobs/{job_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["purpose"] == "user_understanding"
        assert detail.json()["original_filename"] == "notes.txt"
        assert detail.json()["result_preview"] is None
        assert (
            client.post(
                f"/api/v1/knowledge/jobs/{job_id}/process",
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
        persona_upload = client.post(
            "/api/v1/knowledge/documents",
            headers=headers,
            data={"user_qq": "30003", "purpose": "persona_design"},
            files={"document": ("style.md", "# 更自然地交流", "text/markdown")},
        )
        assert persona_upload.status_code == 200
        assert persona_upload.json()["status"] == "awaiting_model_config"
        all_jobs = client.get("/api/v1/knowledge/jobs", headers=headers)
        assert len(all_jobs.json()["items"]) == 2
