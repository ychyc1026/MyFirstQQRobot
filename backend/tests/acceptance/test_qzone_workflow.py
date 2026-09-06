from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from _support.readiness import ALLOW_READINESS, AllowReadinessGuard, BlockReadinessGuard
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import QzoneTaskService
from ych_bot.application.control import OwnerControlService
from ych_bot.application.qzone import QzoneTaskError
from ych_bot.config import Settings
from ych_bot.domain.control import (
    ControlSource,
    OwnerCommand,
    OwnerCommandKind,
    QzoneVisibility,
)
from ych_bot.domain.proactive import MissedTaskPolicy, QuietHoursBehavior
from ych_bot.domain.qzone import QzoneSchedulePolicy
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers import QzonePublishWorker

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeQzonePublisher:
    def __init__(
        self,
        error: Exception | None = None,
        *,
        delete_error: Exception | None = None,
    ) -> None:
        self.error = error
        self.delete_error = delete_error
        self.calls: list[dict[str, object]] = []
        self.deletes: list[str] = []

    async def publish_qzone(
        self,
        *,
        content: str,
        images: list[str] | None = None,
        visibility: int = 1,
        target_uins: list[str] | None = None,
    ) -> str:
        self.calls.append(
            {
                "content": content,
                "images": images,
                "visibility": visibility,
                "target_uins": target_uins,
            }
        )
        if self.error is not None:
            raise self.error
        return f"tid-{len(self.calls)}"

    async def delete_qzone(self, tid: str) -> None:
        self.deletes.append(tid)
        if self.delete_error is not None:
            raise self.delete_error


def components(
    repository: SQLiteRepository,
    clock: MutableClock,
    *,
    worker_enabled: bool = True,
    publish_enabled: bool = True,
    publisher: FakeQzonePublisher | None = None,
    readiness_guard: AllowReadinessGuard | None = None,
) -> tuple[QzoneTaskService, QzonePublishWorker, OwnerControlService, FakeQzonePublisher]:
    service = QzoneTaskService(
        repository,
        owner_qq=OWNER_QQ,
        default_timezone="Asia/Shanghai",
        clock=clock,
    )
    fake = publisher or FakeQzonePublisher()
    worker = QzonePublishWorker(
        repository,
        fake,
        owner_qq=OWNER_QQ,
        configured_enabled=worker_enabled,
        publish_enabled=publish_enabled,
        readiness_guard=readiness_guard or ALLOW_READINESS,
        poll_seconds=1,
        clock=clock,
    )
    controls = OwnerControlService(
        repository,
        fake,  # type: ignore[arg-type]
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        qzone_task_service=service,
        qzone_worker_control=worker,
    )
    return service, worker, controls, fake


async def approve(controls: OwnerControlService, code: str) -> None:
    result = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.APPROVE,
            actor_qq=OWNER_QQ,
            source_message_id=f"test:{uuid4()}",
            arguments={"approval_code": code},
            source=ControlSource.DASHBOARD,
        )
    )
    assert result.data["approved"] is True


async def publish_now(
    service: QzoneTaskService,
    worker: QzonePublishWorker,
    controls: OwnerControlService,
    clock: MutableClock,
    *,
    content: str = "已发布内容",
) -> dict:
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content=content,
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])
    assert (await worker.run_once()).status == "published"
    return await service.post(post["id"])


async def disable_quiet_hours(
    service: QzoneTaskService,
    *,
    daily_limit: int = 20,
    minimum_interval_seconds: int = 0,
) -> None:
    await service.update_policy(
        QzoneSchedulePolicy(
            timezone="Asia/Shanghai",
            quiet_hours_enabled=False,
            quiet_start="22:00",
            quiet_end="08:00",
            quiet_behavior=QuietHoursBehavior.DELAY,
            daily_limit=daily_limit,
            minimum_interval_seconds=minimum_interval_seconds,
        ),
        updated_by=OWNER_QQ,
    )


@pytest.mark.asyncio
async def test_draft_never_creates_approval_or_network_call(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "draft.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, _, publisher = components(repository, clock)

    draft = await service.create_draft(
        content="只保存，不发布",
        created_by=OWNER_QQ,
        source="dashboard",
    )

    assert draft["status"] == "draft"
    assert await repository.pending_approvals() == []
    assert (await worker.run_once()).status == "idle"
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_readiness_blocked_publish_is_terminal_without_network_or_retry(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "publish-readiness.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    guard = BlockReadinessGuard("publish_not_ready")
    service, worker, controls, publisher = components(
        repository,
        clock,
        readiness_guard=guard,
    )
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="绝不触网",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])

    result = await worker.run_once()

    assert result.status == "failed"
    assert result.reason == "readiness_publish_not_ready"
    assert publisher.calls == []
    assert (await worker.run_once()).status == "idle"
    persisted = await service.post(post["id"])
    assert persisted["status"] == "failed"
    assert persisted["last_error"] == "readiness_publish_not_ready"


@pytest.mark.asyncio
async def test_approved_post_publishes_once_and_survives_restart(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "publish.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="经主号审批后发布",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])

    result = await worker.run_once()

    assert result.status == "published"
    stored = await service.post(post["id"])
    assert stored["status"] == "published"
    assert stored["qzone_tid"] == "tid-1"
    assert len(publisher.calls) == 1

    restarted = QzonePublishWorker(
        repository,
        publisher,
        owner_qq=OWNER_QQ,
        configured_enabled=True,
        publish_enabled=True,
        readiness_guard=ALLOW_READINESS,
        poll_seconds=1,
        clock=clock,
    )
    assert (await restarted.run_once()).status == "idle"
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_worker_and_publish_route_are_independent_default_off_gates(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "gates.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(
        repository,
        clock,
        worker_enabled=False,
        publish_enabled=False,
    )
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="两个开关都未开启",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])

    assert (await worker.run_once()).status == "disabled"
    assert publisher.calls == []
    assert (await service.post(post["id"]))["status"] == "scheduled"


@pytest.mark.asyncio
async def test_late_post_requires_fresh_owner_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "late.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="晚到后必须重新确认",
        scheduled_for=clock.current + timedelta(minutes=1),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
        missed_policy=MissedTaskPolicy.REQUIRE_REAPPROVAL,
    )
    await approve(controls, post["approval_code"])
    clock.advance(minutes=3)

    result = await worker.run_once()

    assert result.status == "reapproval_required"
    assert publisher.calls == []
    pending = await repository.pending_approvals()
    assert len(pending) == 1
    assert pending[0]["request_type"] == "qzone.publish.reapprove"
    await approve(controls, pending[0]["approval_code"])
    assert (await worker.run_once()).status == "published"
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_quiet_hours_delay_to_next_local_morning(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "quiet.sqlite3")
    await repository.initialize()
    zone = ZoneInfo("Asia/Shanghai")
    local_now = datetime.now(zone).replace(hour=21, minute=55, second=0, microsecond=0)
    if local_now < datetime.now(zone):
        local_now += timedelta(days=1)
    clock = MutableClock(local_now.astimezone(UTC))
    service, worker, controls, publisher = components(repository, clock)
    post = await service.request_publish(
        content="安静时段后发布",
        scheduled_for=clock.current + timedelta(minutes=10),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])
    clock.advance(minutes=10)

    held = await worker.run_once()

    assert held.status == "held"
    assert held.reason == "quiet_hours"
    stored = await service.post(post["id"])
    assert stored["status"] == "waiting_quiet_hours"
    clock.current = datetime.fromisoformat(stored["next_eligible_at"])
    assert (await worker.run_once()).status == "published"
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_minimum_interval_holds_second_post(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "rate.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service, minimum_interval_seconds=3600)
    posts = []
    for content in ("第一条空间", "第二条空间"):
        post = await service.request_publish(
            content=content,
            scheduled_for=clock.current,
            timezone_name="Asia/Shanghai",
            created_by=OWNER_QQ,
            source="dashboard",
        )
        await approve(controls, post["approval_code"])
        posts.append(post)

    assert (await worker.run_once()).status == "published"
    held = await worker.run_once()

    assert held.status == "held"
    assert held.reason == "minimum_interval"
    assert (await service.post(posts[1]["id"]))["status"] == "waiting_rate_limit"
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_network_exception_is_uncertain_and_never_blindly_retried(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "uncertain.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    publisher = FakeQzonePublisher(TimeoutError("simulated timeout"))
    service, worker, controls, _ = components(
        repository,
        clock,
        publisher=publisher,
    )
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="超时后禁止盲目重发",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])

    result = await worker.run_once()

    assert result.status == "uncertain"
    assert (await service.post(post["id"]))["status"] == "delivery_uncertain"
    assert len(publisher.calls) == 1
    assert (await worker.run_once()).status == "idle"
    assert len(publisher.calls) == 1
    reports = await repository.owner_reports(status="pending")
    assert reports[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_expired_publishing_lease_becomes_uncertain_without_retry(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "restart.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service)
    post = await service.request_publish(
        content="发布中进程中断",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])
    claimed = await repository.claim_due_qzone_post(now=clock.current, lease_seconds=30)
    assert claimed is not None
    assert await repository.begin_qzone_publish(post["id"], now=clock.current)
    clock.advance(seconds=31)

    assert (await worker.run_once()).status == "idle"
    assert (await service.post(post["id"]))["status"] == "delivery_uncertain"
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_owner_qzone_commands_use_real_service_instead_of_deferred(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "commands.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service)
    request = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.QZONE_PUBLISH,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone:1",
            arguments={"content": "主号从 QQ 下令发布"},
        )
    )

    assert request.status == "pending_approval"
    assert request.data["status"] == "pending_approval"
    await approve(controls, request.data["approval_code"])
    assert (await worker.run_once()).status == "published"
    queue = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.QZONE_QUEUE,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone:2",
            arguments={},
        )
    )
    assert queue.data["items"][0]["status"] == "published"
    assert queue.data.get("deferred") is None
    assert len(publisher.calls) == 1
    paused = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.QZONE_PAUSE,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone:3",
            arguments={},
        )
    )
    resumed = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.QZONE_RESUME,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone:4",
            arguments={},
        )
    )
    assert paused.data["paused"] is True
    assert resumed.data["paused"] is False


def test_dashboard_qzone_api_is_visible_but_network_default_off(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=tmp_path / "qzone-api.sqlite3",
            admin_access_token="dashboard-token",
        )
    )

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

        draft = client.post(
            "/api/v1/qzone/drafts",
            headers=headers,
            json={"content": "仪表盘草稿", "visibility": 4},
        )
        assert draft.status_code == 200
        assert draft.json()["status"] == "draft"

        invalid = client.post(
            "/api/v1/qzone/posts",
            headers=headers,
            json={"content": "指定可见但没有目标", "visibility": 16},
        )
        assert invalid.status_code == 422

        targeted = client.post(
            "/api/v1/qzone/posts",
            headers=headers,
            json={
                "content": "仪表盘指定好友可见",
                "visibility": 16,
                "target_uins": ["123456789"],
            },
        )
        assert targeted.status_code == 200
        assert targeted.json()["target_uins"] == ["123456789"]

        request = client.post(
            "/api/v1/qzone/posts",
            headers=headers,
            json={"content": "仪表盘待审批空间", "visibility": 4},
        )
        assert request.status_code == 200
        assert request.json()["status"] == "pending_approval"
        assert request.json()["approval_code"]

        disabled = client.post("/api/v1/qzone/worker/run-once", headers=headers)
        assert disabled.json()["status"] == "disabled"
        posts = client.get("/api/v1/qzone/posts", headers=headers).json()["items"]
        assert {item["status"] for item in posts} == {"draft", "pending_approval"}
        assert sum(1 for item in posts if item["target_uins"] == ["123456789"]) == 1

        status = client.get("/api/v1/status").json()
        assert status["qzone"]["publisher"]["publish_route_enabled"] is False


@pytest.mark.asyncio
async def test_selected_visibility_requires_and_publishes_target_uins(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "targets.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    await disable_quiet_hours(service)

    with pytest.raises(QzoneTaskError, match="requires target QQ"):
        await service.request_publish(
            content="指定好友可见但没有目标",
            scheduled_for=clock.current,
            timezone_name="Asia/Shanghai",
            created_by=OWNER_QQ,
            source="dashboard",
            visibility=QzoneVisibility.SELECTED_FRIENDS,
        )

    post = await service.request_publish(
        content="只给指定好友看",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
        visibility=QzoneVisibility.SELECTED_FRIENDS,
        target_uins=["123456789", "123456789", "987654321"],
    )
    await approve(controls, post["approval_code"])

    assert post["target_uins"] == ["123456789", "987654321"]
    assert (await worker.run_once()).status == "published"
    assert publisher.calls == [
        {
            "content": "只给指定好友看",
            "images": None,
            "visibility": int(QzoneVisibility.SELECTED_FRIENDS),
            "target_uins": ["123456789", "987654321"],
        }
    ]


@pytest.mark.asyncio
async def test_privacy_export_and_delete_cover_targeted_qzone_posts(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-privacy.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, _, controls, _ = components(repository, clock)
    targeted = await service.request_publish(
        content="面向被删除用户的空间",
        scheduled_for=clock.current + timedelta(hours=1),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
        visibility=QzoneVisibility.SELECTED_FRIENDS,
        target_uins=["123456789"],
    )
    public = await service.request_publish(
        content="普通好友可见，不因删除用户而消失",
        scheduled_for=clock.current + timedelta(hours=2),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, targeted["approval_code"])
    await approve(controls, public["approval_code"])

    exported = await repository.privacy_export_snapshot("123456789")
    impact = await repository.privacy_delete_impact("123456789")

    assert exported["qzone_posts"][0]["id"] == targeted["id"]
    assert exported["qzone_posts"][0]["content"] == "面向被删除用户的空间"
    assert exported["qzone_post_runtime"][0]["post_id"] == targeted["id"]
    assert exported["qzone_post_events"]
    assert exported["qzone_approval_requests"]
    assert exported["qzone_owner_reports"]
    assert exported["owner_report_runtime"]
    assert exported["owner_report_events"]
    assert exported["qzone_audit_log"]
    assert impact["targeted_qzone_posts"] == 1
    assert impact["qzone_post_runtime"] == 1
    assert impact["qzone_post_events"] >= 1
    assert impact["approval_requests"] >= 1
    assert impact["owner_reports"] >= 1
    assert impact["owner_report_runtime"] >= 1
    assert impact["owner_report_events"] >= 1

    deleted = await repository.delete_user_data("123456789", request_id="qzone-delete")

    assert deleted["targeted_qzone_posts"] == 1
    assert await repository.qzone_post(targeted["id"]) is None
    remaining = await repository.qzone_post(public["id"])
    assert remaining is not None
    assert remaining["content"] == "普通好友可见，不因删除用户而消失"
    assert (await repository.counts())["qzone_posts"] == 1
    assert (await repository.counts())["qzone_post_runtime"] == 1


@pytest.mark.asyncio
async def test_published_post_requires_approval_before_tid_delete(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "revoke.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    published = await publish_now(service, worker, controls, clock, content="准备撤销")

    revoke = await service.request_revoke(published["id"], actor_qq=OWNER_QQ, source="dashboard")

    assert revoke["status"] == "pending_delete_approval"
    assert revoke["qzone_tid"] == "tid-1"
    assert revoke["approval_code"]
    assert publisher.deletes == []
    pending = await repository.pending_approvals()
    assert pending[0]["request_type"] == "qzone.delete"

    await approve(controls, revoke["approval_code"])
    assert (await service.post(published["id"]))["status"] == "delete_approved"
    result = await worker.run_once()

    assert result.status == "deleted"
    stored = await service.post(published["id"])
    assert stored["status"] == "deleted"
    assert stored["qzone_tid"] == "tid-1"
    assert publisher.deletes == ["tid-1"]
    assert (await worker.run_once()).status == "idle"
    assert publisher.deletes == ["tid-1"]


@pytest.mark.asyncio
async def test_delete_timeout_is_uncertain_and_never_blindly_retried(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "delete-uncertain.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    publisher = FakeQzonePublisher(delete_error=TimeoutError("simulated delete timeout"))
    service, worker, controls, _ = components(repository, clock, publisher=publisher)
    published = await publish_now(service, worker, controls, clock, content="删除超时")
    revoke = await service.request_revoke(published["id"], actor_qq=OWNER_QQ, source="dashboard")
    await approve(controls, revoke["approval_code"])

    result = await worker.run_once()

    assert result.status == "delete_uncertain"
    assert (await service.post(published["id"]))["status"] == "delete_uncertain"
    assert publisher.deletes == ["tid-1"]
    assert (await worker.run_once()).status == "idle"
    assert publisher.deletes == ["tid-1"]
    reports = await repository.owner_reports(status="pending")
    assert any(item["severity"] == "critical" and "删除" in item["title"] for item in reports)


@pytest.mark.asyncio
async def test_expired_deleting_lease_becomes_uncertain_without_retry(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "delete-restart.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    published = await publish_now(service, worker, controls, clock, content="删除中断")
    revoke = await service.request_revoke(published["id"], actor_qq=OWNER_QQ, source="dashboard")
    await approve(controls, revoke["approval_code"])
    claimed = await repository.claim_due_qzone_delete(now=clock.current, lease_seconds=30)
    assert claimed is not None
    assert await repository.begin_qzone_delete(published["id"], now=clock.current)
    clock.advance(seconds=31)

    assert (await worker.run_once()).status == "idle"
    assert (await service.post(published["id"]))["status"] == "delete_uncertain"
    assert publisher.deletes == []


@pytest.mark.asyncio
async def test_revoke_without_tid_or_unpublished_post_is_rejected(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "revoke-invalid.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    publisher = FakeQzonePublisher(TimeoutError("publish timeout"))
    service, worker, controls, _ = components(repository, clock, publisher=publisher)
    await disable_quiet_hours(service)
    draft = await service.create_draft(
        content="草稿不能撤销",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    with pytest.raises(QzoneTaskError, match="cannot be revoked"):
        await service.request_revoke(draft["id"], actor_qq=OWNER_QQ, source="dashboard")

    post = await service.request_publish(
        content="发布不确定没有 tid",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, post["approval_code"])
    assert (await worker.run_once()).status == "uncertain"
    with pytest.raises(QzoneTaskError, match="cannot be revoked"):
        await service.request_revoke(post["id"], actor_qq=OWNER_QQ, source="dashboard")


@pytest.mark.asyncio
async def test_worker_and_publish_route_independently_block_delete(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "delete-gates.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    published = await publish_now(service, worker, controls, clock, content="开关关闭时不删")
    revoke = await service.request_revoke(published["id"], actor_qq=OWNER_QQ, source="dashboard")
    await approve(controls, revoke["approval_code"])

    disabled_worker = QzonePublishWorker(
        repository,
        publisher,
        owner_qq=OWNER_QQ,
        configured_enabled=False,
        publish_enabled=True,
        readiness_guard=ALLOW_READINESS,
        poll_seconds=1,
        clock=clock,
    )
    disabled_route = QzonePublishWorker(
        repository,
        publisher,
        owner_qq=OWNER_QQ,
        configured_enabled=True,
        publish_enabled=False,
        readiness_guard=ALLOW_READINESS,
        poll_seconds=1,
        clock=clock,
    )
    assert (await disabled_worker.run_once()).status == "disabled"
    assert (await disabled_route.run_once()).status == "disabled"
    assert publisher.deletes == []
    assert (await service.post(published["id"]))["status"] == "delete_approved"


@pytest.mark.asyncio
async def test_owner_can_request_qzone_revoke_from_qq(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "revoke-command.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    service, worker, controls, publisher = components(repository, clock)
    published = await publish_now(service, worker, controls, clock, content="主号撤销")

    request = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.QZONE_REVOKE,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone:revoke",
            arguments={"post_id": published["id"]},
            source=ControlSource.DASHBOARD,
        )
    )

    assert request.data["status"] == "pending_delete_approval"
    await approve(controls, request.data["approval_code"])
    assert (await worker.run_once()).status == "deleted"
    assert publisher.deletes == ["tid-1"]


def test_dashboard_can_request_qzone_revoke_while_network_stays_off(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=tmp_path / "qzone-revoke-api.sqlite3",
            admin_access_token="dashboard-token",
        )
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        draft = client.post(
            "/api/v1/qzone/drafts",
            headers=headers,
            json={"content": "草稿不能直接撤销", "visibility": 4},
        )
        assert draft.status_code == 200
        revoke = client.post(
            f"/api/v1/qzone/posts/{draft.json()['id']}/revoke",
            headers=headers,
        )
        assert revoke.status_code == 409
        assert "cannot be revoked" in revoke.json()["detail"]
