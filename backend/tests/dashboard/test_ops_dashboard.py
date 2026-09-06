from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import MessageIngestionService, OpsDashboardService
from ych_bot.application.ops import OpsFlags
from ych_bot.config import Settings
from ych_bot.infrastructure.database import SQLiteRepository


def private_event(*, message_id: int = 901) -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": 10001,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }


@pytest.mark.asyncio
async def test_ops_snapshot_shows_closed_lanes_when_disabled(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "ops-empty.sqlite3")
    await repository.initialize()
    service = OpsDashboardService(
        repository,
        flags=OpsFlags(
            timezone="Asia/Shanghai",
            shadow_enabled=False,
            outbound_enabled=False,
            qzone_publish_enabled=False,
        ),
    )

    snapshot = await service.snapshot(
        workers={
            "knowledge": {"configured_enabled": False, "paused": False},
            "proactive": {"configured_enabled": False, "paused": False},
            "qzone": {"configured_enabled": False, "paused": False},
            "owner_reports": {"configured_enabled": False, "paused": False},
            "outbox": {"configured_enabled": False, "paused": False},
            "image_orphan": {"configured_enabled": False, "paused": False},
        },
        protection={"chat": {"state": "closed"}, "image": {"state": "closed"}},
    )

    assert snapshot["attention"]["needs_owner"] == 0
    assert snapshot["inbound_today"] == 0
    assert snapshot["anomalies"]["total"] == 0
    assert snapshot["lanes"]["shadow"] == {"state": "closed"}
    assert snapshot["lanes"]["outbound"] == {"state": "closed"}
    assert snapshot["lanes"]["qzone"] == {"state": "closed"}
    assert snapshot["workers"][0]["state"] == "未启用"


@pytest.mark.asyncio
async def test_ops_counts_today_inbound_and_series(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "ops-inbound.sqlite3")
    await repository.initialize()
    ingestion = MessageIngestionService(repository, bot_qq="2000000002", enabled=True)
    await ingestion.ingest(private_event())
    await repository.record_owner_report(
        severity="action_required",
        category="ops_test",
        title="需要主号确认",
        body="测试待办",
    )
    service = OpsDashboardService(
        repository,
        flags=OpsFlags(
            timezone="Asia/Shanghai",
            shadow_enabled=True,
            outbound_enabled=False,
            qzone_publish_enabled=False,
        ),
    )

    snapshot = await service.snapshot(
        workers={},
        protection={"chat": {"state": "closed"}, "image": {"state": "closed"}},
    )
    assert snapshot["inbound_today"] == 1
    assert snapshot["attention"]["urgent_reports"] == 1
    assert snapshot["attention"]["needs_owner"] == 1
    assert snapshot["lanes"]["shadow"]["state"] == "open"
    assert snapshot["lanes"]["shadow"]["completed"] == 0
    assert snapshot["lanes"]["outbound"]["state"] == "closed"

    series = await service.series(days=7)
    assert len(series["points"]) == 7
    assert series["points"][-1]["inbound"] == 1
    assert series["points"][-1]["commands"] == 0

    activity = await service.activity(limit=8)
    assert activity["items"]


def test_ops_api_requires_admin_session(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "ops-api.sqlite3",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/ops/snapshot").status_code == 401
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        snapshot = client.get("/api/v1/ops/snapshot", headers=headers)
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["lanes"]["shadow"]["state"] == "closed"
        assert body["lanes"]["outbound"]["state"] == "closed"
        series = client.get("/api/v1/ops/series?days=7", headers=headers)
        assert series.status_code == 200
        assert len(series.json()["points"]) == 7
        activity = client.get("/api/v1/ops/activity", headers=headers)
        assert activity.status_code == 200
        assert "items" in activity.json()
