from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from _support.readiness import ALLOW_READINESS
from ych_bot.application import ProactiveMessageService
from ych_bot.application.control import OwnerControlService
from ych_bot.domain.control import ControlSource, OwnerCommand, OwnerCommandKind
from ych_bot.domain.proactive import MissedTaskPolicy, QuietHoursBehavior
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers import ProactiveMessageWorker
from ych_bot.workers.outbox import OutboxDispatcher

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"
TARGET_QQ = "10001"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeNapCat:
    def __init__(self) -> None:
        self.sent = []

    async def send_message(self, message) -> dict:
        self.sent.append(message)
        return {"status": "ok"}


def services(
    repository: SQLiteRepository,
    clock: MutableClock,
    *,
    worker_enabled: bool = True,
) -> tuple[ProactiveMessageService, ProactiveMessageWorker, OwnerControlService, FakeNapCat]:
    proactive = ProactiveMessageService(
        repository,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        default_timezone="Asia/Shanghai",
        clock=clock,
    )
    worker = ProactiveMessageWorker(
        repository,
        owner_qq=OWNER_QQ,
        configured_enabled=worker_enabled,
        poll_seconds=1,
        policy_recheck_seconds=60,
        clock=clock,
    )
    napcat = FakeNapCat()
    controls = OwnerControlService(
        repository,
        napcat,  # type: ignore[arg-type]
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        proactive_message_service=proactive,
        proactive_worker_control=worker,
    )
    return proactive, worker, controls, napcat


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


async def allow_user(
    proactive: ProactiveMessageService,
    *,
    quiet_hours_enabled: bool = False,
    daily_limit: int = 3,
    minimum_interval_seconds: int = 3600,
) -> None:
    await proactive.update_policy(
        user_qq=TARGET_QQ,
        enabled=True,
        timezone_name="Asia/Shanghai",
        quiet_hours_enabled=quiet_hours_enabled,
        quiet_start="22:00",
        quiet_end="08:00",
        quiet_behavior=QuietHoursBehavior.DELAY,
        daily_limit=daily_limit,
        minimum_interval_seconds=minimum_interval_seconds,
        updated_by=OWNER_QQ,
    )


@pytest.mark.asyncio
async def test_approved_calendar_task_survives_restart_and_enqueues_once(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "proactive.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, napcat = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="按真实日历时间发送",
        scheduled_for=clock.current + timedelta(minutes=5),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, minimum_interval_seconds=0)

    assert (await worker.run_once()).status == "idle"
    clock.advance(minutes=5)
    assert (await worker.run_once()).status == "enqueued"
    assert (await repository.counts())["outbox"] == 1
    assert napcat.sent == []

    restarted = ProactiveMessageWorker(
        repository,
        owner_qq=OWNER_QQ,
        configured_enabled=True,
        poll_seconds=1,
        clock=clock,
    )
    assert (await restarted.run_once()).status == "idle"
    assert (await repository.counts())["outbox"] == 1
    stored = await proactive.task(task["id"])
    assert stored["status"] == "enqueued"
    assert stored["outbox_status"] == "pending"


@pytest.mark.asyncio
async def test_default_skip_marks_task_missed_after_program_downtime(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "missed.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="过期后不要补发",
        scheduled_for=clock.current + timedelta(minutes=1),
        timezone_name="Asia/Shanghai",
        missed_policy=MissedTaskPolicy.SKIP,
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive)
    clock.advance(minutes=3)

    result = await worker.run_once()

    assert result.status == "missed"
    assert (await proactive.task(task["id"]))["status"] == "missed"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_send_within_grace_allows_bounded_catch_up(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "grace.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="允许十分钟内补发",
        scheduled_for=clock.current + timedelta(minutes=1),
        timezone_name="Asia/Shanghai",
        missed_policy=MissedTaskPolicy.SEND_WITHIN_GRACE,
        missed_grace_seconds=600,
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, minimum_interval_seconds=0)
    clock.advance(minutes=3)

    assert (await worker.run_once()).status == "enqueued"
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_quiet_hours_delay_until_next_local_morning(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "quiet.sqlite3")
    await repository.initialize()
    zone = ZoneInfo("Asia/Shanghai")
    local_now = datetime.now(zone).replace(hour=21, minute=55, second=0, microsecond=0)
    if local_now < datetime.now(zone):
        local_now += timedelta(days=1)
    clock = MutableClock(local_now.astimezone(UTC))
    proactive, worker, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="安静时段后再发",
        scheduled_for=clock.current + timedelta(minutes=10),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, quiet_hours_enabled=True, minimum_interval_seconds=0)
    clock.advance(minutes=10)

    held = await worker.run_once()
    assert held.status == "held"
    assert held.reason == "quiet_hours"
    stored = await proactive.task(task["id"])
    assert stored["status"] == "waiting_quiet_hours"
    next_allowed = datetime.fromisoformat(stored["next_eligible_at"])
    clock.current = next_allowed

    assert (await worker.run_once()).status == "enqueued"


@pytest.mark.asyncio
async def test_per_user_minimum_interval_holds_second_task(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "rate.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, _ = services(repository, clock)
    tasks = []
    for content in ("第一条", "第二条"):
        task = await proactive.create_task(
            target_qq=TARGET_QQ,
            content=content,
            scheduled_for=clock.current,
            timezone_name="Asia/Shanghai",
            created_by=OWNER_QQ,
            source="dashboard",
        )
        await approve(controls, task["approval_code"])
        tasks.append(task)
    await allow_user(proactive, minimum_interval_seconds=3600)

    assert (await worker.run_once()).status == "enqueued"
    second = await worker.run_once()
    assert second.status == "held"
    assert second.reason == "minimum_interval"
    assert (await proactive.task(tasks[1]["id"]))["status"] == "waiting_rate_limit"
    assert (await repository.counts())["outbox"] == 1


@pytest.mark.asyncio
async def test_scheduler_and_real_sender_have_independent_gates(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "gates.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, disabled_worker, controls, fake_napcat = services(
        repository,
        clock,
        worker_enabled=False,
    )
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="双重开关",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, minimum_interval_seconds=0)

    assert (await disabled_worker.run_once()).status == "disabled"
    assert (await repository.counts())["outbox"] == 0

    active_worker = ProactiveMessageWorker(
        repository,
        owner_qq=OWNER_QQ,
        configured_enabled=True,
        poll_seconds=1,
        clock=clock,
    )
    assert (await active_worker.run_once()).status == "enqueued"
    outbound_disabled = OutboxDispatcher(
        repository, fake_napcat, enabled=False, readiness_guard=ALLOW_READINESS
    )
    assert await outbound_disabled.run_once() == 0
    assert fake_napcat.sent == []

    fake_sender = OutboxDispatcher(
        repository, fake_napcat, enabled=True, readiness_guard=ALLOW_READINESS
    )
    assert await fake_sender.run_once() == 1
    assert len(fake_napcat.sent) == 1
    assert (await proactive.task(task["id"]))["status"] == "sent"


@pytest.mark.asyncio
async def test_late_task_can_require_fresh_owner_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reapprove.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="晚到时重新确认",
        scheduled_for=clock.current + timedelta(minutes=1),
        timezone_name="Asia/Shanghai",
        missed_policy=MissedTaskPolicy.REQUIRE_REAPPROVAL,
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, minimum_interval_seconds=0)
    clock.advance(minutes=3)

    result = await worker.run_once()

    assert result.status == "reapproval_required"
    assert (await proactive.task(task["id"]))["status"] == "reapproval_required"
    pending = await repository.pending_approvals()
    assert len(pending) == 1
    assert pending[0]["request_type"] == "proactive_message.reapprove"
    await approve(controls, pending[0]["approval_code"])
    assert (await worker.run_once()).status == "enqueued"


@pytest.mark.asyncio
async def test_privacy_export_and_delete_include_proactive_data(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, _, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="属于目标用户的主动消息",
        scheduled_for=clock.current + timedelta(hours=1),
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive)

    exported = await repository.privacy_export_snapshot(TARGET_QQ)
    impact = await repository.privacy_delete_impact(TARGET_QQ)

    assert exported["proactive_user_policy"][0]["user_qq"] == TARGET_QQ
    assert exported["proactive_message_tasks"][0]["content"] == "属于目标用户的主动消息"
    assert exported["proactive_delivery_events"]
    assert exported["proactive_approval_requests"]
    assert impact["proactive_message_tasks"] == 1
    assert impact["proactive_delivery_events"] >= 2

    deleted = await repository.delete_user_data(TARGET_QQ, request_id="proactive-delete")

    assert deleted["proactive_message_tasks"] == 1
    assert await repository.proactive_task(task["id"]) is None
    assert await repository.proactive_user_policy(TARGET_QQ) is None


@pytest.mark.asyncio
async def test_owner_can_cancel_enqueued_message_before_delivery(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "cancel-enqueued.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC).replace(microsecond=0))
    proactive, worker, controls, _ = services(repository, clock)
    task = await proactive.create_task(
        target_qq=TARGET_QQ,
        content="尚未真正发送，可以撤销",
        scheduled_for=clock.current,
        timezone_name="Asia/Shanghai",
        created_by=OWNER_QQ,
        source="dashboard",
    )
    await approve(controls, task["approval_code"])
    await allow_user(proactive, minimum_interval_seconds=0)
    assert (await worker.run_once()).status == "enqueued"

    cancelled = await proactive.cancel(task["id"], actor_qq=OWNER_QQ)

    assert cancelled["status"] == "cancelled"
    assert cancelled["outbox_id"] is None
    assert (await repository.counts())["outbox"] == 0
