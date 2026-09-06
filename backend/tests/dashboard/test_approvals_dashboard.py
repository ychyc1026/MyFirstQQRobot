import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings
from ych_bot.domain.control import ReportSeverity
from ych_bot.infrastructure.database import SQLiteRepository


def test_dashboard_can_list_and_ack_owner_reports(tmp_path: Path) -> None:
    database_path = tmp_path / "approvals-ui.sqlite3"

    async def seed() -> str:
        repository = SQLiteRepository(database_path)
        await repository.initialize()
        recorded = await repository.record_owner_report(
            severity=ReportSeverity.ACTION_REQUIRED,
            category="quota",
            title="用户今日额度已用尽",
            body="QQ 30003 已用尽今日额度。",
            related_type="private_user",
            related_id="30003",
            updated_by="10001",
            now=datetime.now(UTC),
        )
        return recorded["id"]

    report_id = asyncio.run(seed())
    settings = Settings(
        project_root=tmp_path,
        database_path=database_path,
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
        listed = client.get(
            "/api/v1/owner/reports",
            headers=headers,
            params={"status": "pending"},
        )
        assert listed.status_code == 200
        assert any(item["id"] == report_id for item in listed.json()["items"])
        detail = client.get(f"/api/v1/owner/reports/{report_id}", headers=headers)
        assert detail.status_code == 200
        assert "30003" in detail.json()["body"]
        summary = client.get("/api/v1/owner/reports/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["outbound_enabled"] is False
        ack = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "report.ack", "arguments": {"report_id": report_id}},
        )
        assert ack.status_code == 200
        assert ack.json()["data"]["acknowledged"] is True
        remaining = client.get(
            "/api/v1/owner/reports",
            headers=headers,
            params={"status": "pending"},
        )
        assert remaining.json()["items"] == []
