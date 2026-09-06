from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from _support.readiness import ALLOW_READINESS, BlockReadinessGuard
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application.control import OwnerControlService
from ych_bot.application.qzone_profile import QzoneProfileService
from ych_bot.config import Settings
from ych_bot.domain.control import ControlSource, OwnerCommand, OwnerCommandKind
from ych_bot.domain.identity import FriendState, QzoneProfileAccessMode
from ych_bot.domain.memory import MemorySource, MemoryStatus
from ych_bot.infrastructure.database import SQLiteRepository

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"
USER_QQ = "123456789"


class FakeQzoneProfileClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def get_qzone_msg_list(self, *, user_qq: str, count: int) -> dict:
        self.calls.append({"user_qq": user_qq, "count": count})
        return {
            "nickname": "小明",
            "signature": "喜欢喝茶",
            "items": [
                {
                    "tid": "tid-profile-1",
                    "content": "周末去爬山",
                    "published_at": "2026-08-10T02:00:00+00:00",
                }
            ],
        }


@pytest.mark.asyncio
async def test_existing_friend_does_not_authorize_qzone_profile_read(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-deny.sqlite3")
    await repository.initialize()
    await repository.set_friend_state(
        user_qq=USER_QQ,
        state=FriendState.EXISTING_FRIEND,
        updated_by=OWNER_QQ,
        reason="test",
    )
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is False
    assert result.reason == "qzone_profile_mode_deny"
    assert client.calls == []
    assert (await repository.counts())["qzone_profile_snapshots"] == 0
    assert (await repository.counts())["memory_records"] == 0
    assert (await repository.counts())["data_access_log"] == 1


@pytest.mark.asyncio
async def test_global_switch_blocks_before_network_even_with_grant(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-off.sqlite3")
    await repository.initialize()
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.ONE_TIME,
        max_items=10,
        one_time_remaining=1,
        updated_by=OWNER_QQ,
        reason="test",
    )
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=False,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is False
    assert result.reason == "qzone_profile_collection_disabled"
    assert client.calls == []


@pytest.mark.asyncio
async def test_one_time_grant_creates_expiring_candidates_not_active_memory(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-once.sqlite3")
    await repository.initialize()
    clock = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
        clock=lambda: clock,
        candidate_ttl_days=7,
    )
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.ONE_TIME,
        max_items=10,
        one_time_remaining=1,
        updated_by=OWNER_QQ,
        reason="approved:test",
    )

    first = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)
    second = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert first.allowed is True
    assert first.snapshot is not None
    assert first.snapshot["nickname"] == "小明"
    assert first.candidates
    assert all(item["status"] == MemoryStatus.CANDIDATE.value for item in first.candidates)
    assert all(item["source"] == MemorySource.QZONE_DERIVED.value for item in first.candidates)
    assert all(
        item["expires_at"] == (clock + timedelta(days=7)).isoformat() for item in first.candidates
    )
    assert second.allowed is False
    assert second.reason == "one_time_authorization_consumed"
    assert client.calls == [{"user_qq": USER_QQ, "count": 10}]
    memories = await repository.memory_records(USER_QQ)
    assert memories
    assert all(item["status"] == MemoryStatus.CANDIDATE.value for item in memories)
    assert (await repository.counts())["qzone_profile_snapshots"] == 1


@pytest.mark.asyncio
async def test_readiness_block_preserves_one_time_qzone_profile_grant(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-readiness.sqlite3")
    await repository.initialize()
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.ONE_TIME,
        max_items=10,
        one_time_remaining=1,
        updated_by=OWNER_QQ,
        reason="approved:test",
    )
    client = FakeQzoneProfileClient()
    blocked_service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=BlockReadinessGuard("profile_not_ready"),  # type: ignore[arg-type]
        collection_enabled=True,
    )
    allowed_service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    blocked = await blocked_service.preview(
        USER_QQ,
        purpose="initialize_user_profile",
        actor_qq=OWNER_QQ,
    )
    allowed = await allowed_service.preview(
        USER_QQ,
        purpose="initialize_user_profile",
        actor_qq=OWNER_QQ,
    )

    assert blocked.allowed is False
    assert blocked.reason == "readiness_profile_not_ready"
    assert allowed.allowed is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_freeze_blocks_qzone_profile_preview(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-freeze.sqlite3")
    await repository.initialize()
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.TTL,
        max_items=5,
        expires_at=datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
        updated_by=OWNER_QQ,
        reason="test",
    )
    await repository.set_user_frozen(user_qq=USER_QQ, frozen=True, updated_by=OWNER_QQ)
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is False
    assert result.reason == "user_data_frozen"
    assert client.calls == []


@pytest.mark.asyncio
async def test_expired_ttl_grant_is_denied(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-ttl.sqlite3")
    await repository.initialize()
    now = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.TTL,
        max_items=5,
        expires_at=(now - timedelta(hours=1)).isoformat(),
        updated_by=OWNER_QQ,
        reason="test",
    )
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
        clock=lambda: now,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is False
    assert result.reason == "qzone_profile_authorization_expired"
    assert client.calls == []


@pytest.mark.asyncio
async def test_owner_can_query_and_request_qzone_profile_grant(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-control.sqlite3")
    await repository.initialize()
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )
    controls = OwnerControlService(
        repository,
        client,  # type: ignore[arg-type]
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        qzone_profile_service=service,
    )

    status = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.USER_QZONE_PROFILE_STATUS,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone-profile:1",
            arguments={"user_qq": USER_QQ},
            source=ControlSource.DASHBOARD,
        )
    )
    grant = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.USER_QZONE_PROFILE_ONE_TIME,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone-profile:2",
            arguments={"user_qq": USER_QQ, "max_items": "8"},
            source=ControlSource.DASHBOARD,
        )
    )

    assert status.data["qzone_profile_policy"]["mode"] == QzoneProfileAccessMode.DENY.value
    assert grant.status == "pending_approval"
    assert grant.data["request_type"] == "qzone_profile.one_time"


def test_dashboard_qzone_profile_preview_stays_off_by_default(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=tmp_path / "qzone-profile-api.sqlite3",
            admin_access_token="dashboard-token",
        )
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        privacy = client.get("/api/v1/privacy/status", headers=headers).json()
        assert privacy["qzone_profile_collection_enabled"] is False
        preview = client.post(
            f"/api/v1/users/{USER_QQ}/qzone-profile/preview",
            headers=headers,
            json={"purpose": "initialize_user_profile"},
        )
        assert preview.status_code == 200
        assert preview.json()["allowed"] is False
        assert preview.json()["reason"] == "qzone_profile_collection_disabled"


class ScriptedQzoneProfileClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def get_qzone_msg_list(self, *, user_qq: str, count: int) -> dict:
        self.calls.append({"user_qq": user_qq, "count": count})
        items = list(self.payload.get("items") or [])
        return {
            "nickname": self.payload.get("nickname"),
            "signature": self.payload.get("signature"),
            "items": items[:count],
        }


def _outbox_rows(repository: SQLiteRepository) -> list[dict[str, object]]:
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT target_id, conversation_kind, segments_json FROM outbox"
        ).fetchall()
    return [
        {
            "target_id": row["target_id"],
            "conversation_kind": row["conversation_kind"],
            "segments": json.loads(row["segments_json"]),
        }
        for row in rows
    ]


async def _grant(repository: SQLiteRepository, *, max_items: int = 10) -> None:
    await repository.set_qzone_profile_policy(
        user_qq=USER_QQ,
        mode=QzoneProfileAccessMode.ONE_TIME,
        max_items=max_items,
        one_time_remaining=1,
        updated_by=OWNER_QQ,
        reason="approved:test",
    )


@pytest.mark.asyncio
async def test_preview_keeps_fewer_than_ten_posts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-few.sqlite3")
    await repository.initialize()
    await _grant(repository)
    client = ScriptedQzoneProfileClient(
        {
            "items": [
                {"tid": "a", "content": "一条"},
                {"tid": "b", "content": "两条"},
            ]
        }
    )
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is True
    assert result.summary is not None
    assert result.summary["scanned"] == 2
    assert client.calls == [{"user_qq": USER_QQ, "count": 10}]


@pytest.mark.asyncio
async def test_control_rejects_more_than_twenty_profile_items(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-cap.sqlite3")
    await repository.initialize()
    controls = OwnerControlService(
        repository,
        FakeQzoneProfileClient(),  # type: ignore[arg-type]
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
    )
    result = await controls.execute(
        OwnerCommand(
            id=str(uuid4()),
            kind=OwnerCommandKind.USER_QZONE_PROFILE_ONE_TIME,
            actor_qq=OWNER_QQ,
            source_message_id="owner:qzone-profile:cap",
            arguments={"user_qq": USER_QQ, "max_items": "21"},
            source=ControlSource.DASHBOARD,
        )
    )
    assert result.status == "failed"
    assert "20" in str(result.data.get("message") or "")


@pytest.mark.asyncio
async def test_video_is_not_persisted_and_unread_without_cover(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-video.sqlite3")
    await repository.initialize()
    await _grant(repository)
    client = ScriptedQzoneProfileClient(
        {
            "items": [
                {
                    "tid": "vid-1",
                    "content": "",
                    "has_video": True,
                    "video_url": "https://example.invalid/huge.mp4",
                }
            ]
        }
    )
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    assert result.allowed is True
    assert result.summary is not None
    assert result.summary["videos_unread"] == 1
    public = result.public_dict()
    dumped = json.dumps(public, ensure_ascii=False)
    assert "huge.mp4" not in dumped
    assert "周末" not in dumped
    snapshot = await repository.latest_qzone_profile_snapshot(USER_QQ)
    assert snapshot is not None
    assert "huge.mp4" not in json.dumps(snapshot, ensure_ascii=False)


@pytest.mark.asyncio
async def test_preview_acks_owner_without_post_bodies(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-ack.sqlite3")
    await repository.initialize()
    await _grant(repository)
    client = FakeQzoneProfileClient()
    service = QzoneProfileService(
        repository,
        client,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(
        USER_QQ,
        purpose="initialize_user_profile",
        actor_qq=OWNER_QQ,
        source="dashboard",
    )

    assert result.allowed is True
    outbox = _outbox_rows(repository)
    assert len(outbox) == 1
    assert outbox[0]["target_id"] == OWNER_QQ
    assert outbox[0]["conversation_kind"] == "private"
    text = json.dumps(outbox[0]["segments"], ensure_ascii=False)
    assert USER_QQ in text
    assert "仪表盘" in text
    assert "周末去爬山" not in text
    assert "小明" not in text


@pytest.mark.asyncio
async def test_denied_preview_still_acks_owner(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-ack-deny.sqlite3")
    await repository.initialize()
    service = QzoneProfileService(
        repository,
        FakeQzoneProfileClient(),
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )

    result = await service.preview(
        USER_QQ,
        purpose="initialize_user_profile",
        actor_qq=OWNER_QQ,
        source="command",
    )

    assert result.allowed is False
    outbox = _outbox_rows(repository)
    assert len(outbox) == 1
    text = json.dumps(outbox[0]["segments"], ensure_ascii=False)
    assert "未读取" in text
    assert "qzone_profile_mode_deny" in text


@pytest.mark.asyncio
async def test_user_detail_collection_card_omits_bodies(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qzone-profile-card.sqlite3")
    await repository.initialize()
    await _grant(repository)
    service = QzoneProfileService(
        repository,
        FakeQzoneProfileClient(),
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        readiness_guard=ALLOW_READINESS,
        collection_enabled=True,
    )
    await service.preview(USER_QQ, purpose="initialize_user_profile", actor_qq=OWNER_QQ)

    detail = await repository.user_detail(USER_QQ)
    card = detail["latest_collection"]
    assert card["scanned"] >= 1
    assert "items" not in card
    assert "周末去爬山" not in json.dumps(detail, ensure_ascii=False)
