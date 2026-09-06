from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from _support.readiness import ALLOW_READINESS
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application.control import OwnerControlService
from ych_bot.application.reports import OwnerReportService
from ych_bot.config import Settings
from ych_bot.domain.control import OwnerCommand, OwnerCommandKind, ReportSeverity
from ych_bot.domain.reports import ReportDeliveryPolicy, ReportDeliveryStatus
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers.outbox import OutboxDispatcher
from ych_bot.workers.reports import OwnerReportWorker

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeNapCat:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[object] = []

    async def send_message(self, message) -> dict:
        self.sent.append(message)
        if self.error is not None:
            raise self.error
        return {"status": "ok"}


def components(
    repository: SQLiteRepository,
    clock: MutableClock,
    *,
    worker_enabled: bool = True,
    reports_enabled: bool = True,
    outbound_enabled: bool = True,
    publisher: FakeNapCat | None = None,
) -> tuple[
    OwnerReportService, OwnerReportWorker, OutboxDispatcher, OwnerControlService, FakeNapCat
]:
    service = OwnerReportService(
        repository,
        owner_qq=OWNER_QQ,
        default_timezone="Asia/Shanghai",
        clock=clock,
    )
    fake = publisher or FakeNapCat()
    worker = OwnerReportWorker(
        repository,
        owner_qq=OWNER_QQ,
        configured_enabled=worker_enabled,
        reports_enabled=reports_enabled,
        poll_seconds=1,
        clock=clock,
    )
    dispatcher = OutboxDispatcher(
        repository,
        fake,  # type: ignore[arg-type]
        enabled=outbound_enabled,
        readiness_guard=ALLOW_READINESS,
        poll_seconds=1,
        max_attempts=1,
        retry_base_seconds=30,
    )
    controls = OwnerControlService(
        repository,
        fake,  # type: ignore[arg-type]
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        owner_report_service=service,
        owner_report_worker_control=worker,
        outbox_worker_control=dispatcher,
    )
    return service, worker, dispatcher, controls, fake


@pytest.mark.asyncio
async def test_duplicate_warning_merges_inside_window_and_does_not_enqueue_twice(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "merge.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime(2026, 8, 13, 10, 0, tzinfo=UTC))
    service, worker, dispatcher, _, fake = components(repository, clock)

    first = await service.record(
        severity=ReportSeverity.CRITICAL,
        category="qzone",
        title="发布结果不确定",
        body="空间任务 A 超时",
        related_type="qzone_post",
        related_id="post-a",
    )
    second = await service.record(
        severity=ReportSeverity.CRITICAL,
        category="qzone",
        title="发布结果不确定",
        body="空间任务 A 再次超时",
        related_type="qzone_post",
        related_id="post-a",
    )

    assert first["id"] == second["id"]
    assert second["merged"] is True
    assert second["occurrence_count"] == 2
    assert (await repository.counts())["owner_reports"] == 1

    assert (await worker.run_once()).status == "queued"
    assert (await dispatcher.run_once()) == 1
    assert len(fake.sent) == 1
    assert (await worker.run_once()).status == "delivered"
    assert (await worker.run_once()).status == "idle"
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_info_report_waits_for_digest_local_time(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "digest.sqlite3")
    await repository.initialize()
    zone = ZoneInfo("Asia/Shanghai")
    local_evening = datetime(2026, 8, 13, 21, 0, tzinfo=zone)
    clock = MutableClock(local_evening.astimezone(UTC))
    service, worker, dispatcher, _, fake = components(repository, clock)

    report = await service.record(
        severity=ReportSeverity.INFO,
        category="system",
        title="每日运行摘要",
        body="今天没有异常",
        related_type="system",
        related_id="daily",
    )

    assert report["delivery_policy"] == ReportDeliveryPolicy.DIGEST.value
    assert report["delivery_status"] == ReportDeliveryStatus.WAITING_DIGEST.value
    assert (await worker.run_once()).status == "idle"
    assert fake.sent == []

    clock.current = datetime.fromisoformat(report["next_eligible_at"])
    assert (await worker.run_once()).status == "queued"
    assert (await dispatcher.run_once()) == 1
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_worker_route_and_outbound_are_independent_default_off_gates(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "gates.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, dispatcher, _, fake = components(
        repository,
        clock,
        worker_enabled=False,
        reports_enabled=False,
        outbound_enabled=False,
    )
    await service.record(
        severity=ReportSeverity.ACTION_REQUIRED,
        category="image_generation",
        title="图片待审批",
        body="确认码 ABC",
        related_type="image_task",
        related_id=str(uuid4()),
    )

    assert (await worker.run_once()).status == "disabled"
    assert fake.sent == []
    assert (await dispatcher.run_once()) == 0
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_dashboard_only_never_enters_outbox(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "dashboard-only.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, _, _, fake = components(repository, clock)
    await service.update_policy(
        info_policy=ReportDeliveryPolicy.DASHBOARD_ONLY,
        updated_by=OWNER_QQ,
    )
    report = await service.record(
        severity=ReportSeverity.INFO,
        category="audit",
        title="仅仪表盘可见",
        body="不私聊主号",
        related_type="system",
        related_id="audit-1",
    )

    assert report["delivery_policy"] == ReportDeliveryPolicy.DASHBOARD_ONLY.value
    assert (await worker.run_once()).status == "suppressed"
    assert fake.sent == []
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_send_failure_is_recorded_and_never_re_enqueued(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "fail.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    publisher = FakeNapCat(RuntimeError("napcat down"))
    service, worker, dispatcher, _, fake = components(
        repository,
        clock,
        publisher=publisher,
    )
    report = await service.record(
        severity=ReportSeverity.CRITICAL,
        category="auth",
        title="鉴权异常",
        body="OneBot token rejected",
        related_type="system",
        related_id="auth",
    )

    assert (await worker.run_once()).status == "queued"
    assert (await dispatcher.run_once()) == 0
    assert len(fake.sent) == 1
    assert (await worker.run_once()).status == "failed"
    stored = await service.report(report["id"])
    assert stored["delivery_status"] == ReportDeliveryStatus.FAILED.value
    assert (await worker.run_once()).status == "idle"
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_owner_can_query_and_pause_report_worker(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "commands.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, _, controls, fake = components(repository, clock)
    created = await service.record(
        severity=ReportSeverity.ACTION_REQUIRED,
        category="knowledge_processing",
        title="资料待审批",
        body="确认码 XYZ",
        related_type="knowledge_job",
        related_id=str(uuid4()),
    )
    queue = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.REPORT_QUEUE,
            actor_qq=OWNER_QQ,
            source_message_id="owner:report:1",
            arguments={},
        )
    )
    assert queue.data["items"][0]["id"] == created["id"]
    paused = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.REPORT_WORKER_PAUSE,
            actor_qq=OWNER_QQ,
            source_message_id="owner:report:2",
            arguments={},
        )
    )
    resumed = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.REPORT_WORKER_RESUME,
            actor_qq=OWNER_QQ,
            source_message_id="owner:report:3",
            arguments={},
        )
    )
    assert paused.data["paused"] is True
    assert resumed.data["paused"] is False
    assert fake.sent == []
    assert worker.active is True


def test_dashboard_report_api_is_visible_but_network_default_off(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=tmp_path / "report-api.sqlite3",
            admin_access_token="dashboard-token",
        )
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        summary = client.get("/api/v1/owner/reports/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["worker"]["active"] is False
        assert summary.json()["delivery_route_enabled"] is False
        worker = client.post("/api/v1/owner/reports/worker/run-once", headers=headers)
        assert worker.json()["status"] == "disabled"
        status = client.get("/api/v1/status").json()
        assert status["owner_reports"]["worker"]["delivery_route_enabled"] is False
