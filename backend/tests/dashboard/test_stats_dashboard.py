from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application.context import ConversationContextService
from ych_bot.application.diary import DiaryService
from ych_bot.application.materials import MaterialService
from ych_bot.application.memory import MemoryService
from ych_bot.application.personas import PersonaService
from ych_bot.application.proactive import ProactiveMessageService
from ych_bot.application.proactive_content import ProactiveContentComposer
from ych_bot.application.search import WebSearchClient
from ych_bot.application.stats import StatsService
from ych_bot.application.summaries import DailySummaryService
from ych_bot.config import Settings
from ych_bot.domain.modeling import ChatGenerationResult, DisabledChatModelGateway
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.domain.proactive import QuietHoursBehavior
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event


class FakeChatGateway:
    def __init__(self, text: str = "可以聊聊这件事。") -> None:
        self.text = text
        self.calls = 0

    async def generate(self, _request: object) -> ChatGenerationResult:
        self.calls += 1
        return ChatGenerationResult(text=self.text, input_tokens=3, output_tokens=2)

    async def close(self) -> None:
        return None


class FakeSearch:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.calls = 0

    async def search(self, _query: str) -> str:
        self.calls += 1
        return "外部检索结果"


def _client(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "stats.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)
    return TestClient(app), settings


def test_stats_chatlog_diary_and_summary_endpoints(tmp_path: Path) -> None:
    client, _settings = _client(tmp_path)
    with client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        overview = client.get("/api/v1/stats/overview?range=today", headers=headers)
        assert overview.status_code == 200
        body = overview.json()
        assert body["private_users"] == 0
        assert body["tokens"]["total_tokens"] == 0
        peers = client.get("/api/v1/chatlog/peers", headers=headers)
        assert peers.json()["items"] == []
        saved = client.put(
            "/api/v1/diary",
            headers=headers,
            json={"day_key": "2026-08-14", "content": "今天去看海。"},
        )
        assert saved.status_code == 200
        assert saved.json()["status"] == "pending"
        listed = client.get("/api/v1/diary", headers=headers)
        assert listed.json()["items"][0]["content"] == "今天去看海。"
        summary = client.post("/api/v1/stats/summary?date=2026-08-14", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["entries"] == []
        cached = client.get("/api/v1/stats/summary?date=2026-08-14", headers=headers)
        assert cached.json()["generated_at"] == summary.json()["generated_at"]
        material = client.post(
            "/api/v1/materials",
            headers=headers,
            json={
                "title": "一本书",
                "content": "这是一本小说。",
                "uploader_claim": "一本小说",
            },
        )
        assert material.status_code == 200
        assert material.json()["ai_verdict"] == "mismatch"


@pytest.mark.asyncio
async def test_diary_auto_content_enqueues_without_model(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "auto-diary.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC)
    stats = StatsService(repository, timezone_name="Asia/Shanghai")
    today = now.astimezone(stats._timezone).date().isoformat()
    diary = DiaryService(repository, timezone_name="Asia/Shanghai")
    await diary.save(day_key=today, content="今天写了一段给朋友看的日记。")
    proactive = ProactiveMessageService(
        repository,
        owner_qq="10001",
        bot_qq="20002",
        default_timezone="Asia/Shanghai",
    )
    await proactive.update_policy(
        user_qq="30003",
        enabled=True,
        timezone_name="Asia/Shanghai",
        quiet_hours_enabled=False,
        quiet_start="22:00",
        quiet_end="08:00",
        quiet_behavior=QuietHoursBehavior.DELAY,
        daily_limit=3,
        minimum_interval_seconds=0,
        send_diary=True,
        updated_by="10001",
    )
    composer = ProactiveContentComposer(
        repository,
        stats,
        DisabledChatModelGateway(),
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
        WebSearchClient(enabled=False, api_base="", api_key=""),
        owner_qq="10001",
        bot_qq="20002",
        model_route="",
        enabled=False,
    )
    sent = await composer.run_daily(now)
    assert sent == 1
    counts = await repository.counts()
    assert counts["outbox"] == 1
    assert counts["proactive_message_tasks"] == 1
    stored = await repository.diary_entry(today)
    assert stored is not None
    assert stored["status"] == "pending"
    claimed = await repository.claim_outbound()
    assert claimed is not None
    await repository.mark_outbound_sent(claimed.id, bot_qq="20002")
    stored = await repository.diary_entry(today)
    assert stored is not None
    assert stored["status"] == "sent"
    sent_again = await composer.run_daily(now)
    assert sent_again == 0

    summaries = DailySummaryService(
        repository,
        stats,
        DisabledChatModelGateway(),
        owner_qq="10001",
        bot_qq="20002",
        model_route="",
        enabled=False,
    )
    first = await summaries.generate(today)
    second = await summaries.generate(today)
    assert first["generated_at"] == second["generated_at"]


async def _enable_diary_policy(
    repository: SQLiteRepository,
    user_qq: str,
    *,
    daily_limit: int = 3,
    minimum_interval_seconds: int = 0,
    timezone_name: str = "Asia/Shanghai",
) -> None:
    proactive = ProactiveMessageService(
        repository,
        owner_qq="10001",
        bot_qq="20002",
        default_timezone="Asia/Shanghai",
    )
    await proactive.update_policy(
        user_qq=user_qq,
        enabled=True,
        timezone_name=timezone_name,
        quiet_hours_enabled=False,
        quiet_start="22:00",
        quiet_end="08:00",
        quiet_behavior=QuietHoursBehavior.DELAY,
        daily_limit=daily_limit,
        minimum_interval_seconds=minimum_interval_seconds,
        send_diary=True,
        updated_by="10001",
    )


def _composer(repository: SQLiteRepository, stats: StatsService) -> ProactiveContentComposer:
    return ProactiveContentComposer(
        repository,
        stats,
        DisabledChatModelGateway(),
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
        WebSearchClient(enabled=False, api_base="", api_key=""),
        owner_qq="10001",
        bot_qq="20002",
        model_route="",
        enabled=False,
    )


@pytest.mark.asyncio
async def test_diary_sends_to_each_user_and_waits_for_outbox(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "diary-multi.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC)
    stats = StatsService(repository, timezone_name="Asia/Shanghai")
    today = now.astimezone(stats.timezone).date().isoformat()
    await DiaryService(repository, timezone_name="Asia/Shanghai").save(
        day_key=today,
        content="同一篇日记发给两个好友。",
    )
    await _enable_diary_policy(repository, "30003")
    await _enable_diary_policy(repository, "30004")
    composer = _composer(repository, stats)
    assert await composer.run_daily(now) == 2
    stored = await repository.diary_entry(today)
    assert stored is not None
    assert stored["status"] == "pending"
    first = await repository.claim_outbound()
    second = await repository.claim_outbound()
    assert {first.target_id, second.target_id} == {"30003", "30004"}
    await repository.mark_outbound_sent(first.id, bot_qq="20002")
    stored = await repository.diary_entry(today)
    assert stored is not None
    assert stored["status"] == "sent"
    assert await composer.run_daily(now) == 0


@pytest.mark.asyncio
async def test_diary_bypasses_prior_auto_content_and_daily_limit(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "diary-bypass.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC)
    stats = StatsService(repository, timezone_name="Asia/Shanghai")
    today = now.astimezone(stats.timezone).date().isoformat()
    await DiaryService(repository, timezone_name="Asia/Shanghai").save(
        day_key=today,
        content="限额满了也要发日记。",
    )
    await _enable_diary_policy(repository, "30003", daily_limit=1, minimum_interval_seconds=3600)
    await repository.enqueue_auto_proactive_message(
        target_qq="30003",
        content="先发过一条上文。",
        content_source="context",
        timezone_name="Asia/Shanghai",
        created_by="10001",
        now=now,
    )
    composer = _composer(repository, stats)
    assert await composer.run_daily(now) == 1
    assert (await repository.counts())["outbox"] == 2


@pytest.mark.asyncio
async def test_chatlog_invalid_after_id_does_not_replay(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "chatlog-after.sqlite3")
    await repository.initialize()
    for index, stamp in enumerate((1_700_000_100, 1_700_000_200), start=1):
        payload = {
            "time": stamp,
            "self_id": 20002,
            "post_type": "message",
            "message_type": "private",
            "message_id": 800 + index,
            "user_id": 30003,
            "message": [{"type": "text", "data": {"text": f"msg-{index}"}}],
        }
        assert await repository.store_inbound(parse_message_event(payload, expected_bot_qq="20002"))
    start = "2020-01-01T00:00:00+00:00"
    end = "2100-01-01T00:00:00+00:00"
    items = await repository.chatlog_messages(
        kind="private",
        peer_id="30003",
        start_iso=start,
        end_iso=end,
    )
    assert [item["plain_text"] for item in items] == ["msg-1", "msg-2"]
    replayed = await repository.chatlog_messages(
        kind="private",
        peer_id="30003",
        start_iso=start,
        end_iso=end,
        after_id="missing-id",
    )
    assert replayed == []
    later = await repository.chatlog_messages(
        kind="private",
        peer_id="30003",
        start_iso=start,
        end_iso=end,
        after_id=items[0]["id"],
    )
    assert [item["plain_text"] for item in later] == ["msg-2"]


@pytest.mark.asyncio
async def test_stats_daily_trend_uses_named_timezone(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "stats-tz.sqlite3")
    await repository.initialize()
    occurred = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    payload = {
        "time": int(occurred.timestamp()),
        "self_id": 20002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 901,
        "user_id": 30003,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }
    assert await repository.store_inbound(parse_message_event(payload, expected_bot_qq="20002"))
    trend = await repository.stats_daily_messages(
        start_iso="2026-08-13T00:00:00+00:00",
        end_iso="2026-08-15T00:00:00+00:00",
        timezone_name="America/New_York",
    )
    by_day = {item["day"]: item for item in trend}
    assert by_day["2026-08-13"]["inbound"] == 1
    assert by_day.get("2026-08-14", {"inbound": 0})["inbound"] == 0


@pytest.mark.asyncio
async def test_diary_sent_uses_stats_day_not_policy_timezone(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "diary-tz.sqlite3")
    await repository.initialize()
    now = datetime(2026, 8, 14, 17, 0, tzinfo=UTC)
    stats = StatsService(repository, timezone_name="Asia/Shanghai")
    today = now.astimezone(stats.timezone).date().isoformat()
    assert today == "2026-08-15"
    await DiaryService(repository, timezone_name="Asia/Shanghai").save(
        day_key=today,
        content="跨时区也要标成已发。",
    )
    await _enable_diary_policy(repository, "30003", timezone_name="America/New_York")
    composer = _composer(repository, stats)
    assert await composer.run_daily(now) == 1
    with repository._connect() as connection:
        row = connection.execute("SELECT id FROM outbox WHERE status = 'pending'").fetchone()
    assert row is not None
    await repository.mark_outbound_sent(str(row["id"]), bot_qq="20002")
    stored = await repository.diary_entry(today)
    assert stored is not None
    assert stored["status"] == "sent"


@pytest.mark.asyncio
async def test_auto_content_requires_independent_permission_and_respects_quiet_hours(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "auto-content-gate.sqlite3")
    await repository.initialize()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    stats = StatsService(repository, timezone_name="UTC")
    material = await repository.create_proactive_material(
        title="测试材料",
        content="这是一段可以聊的话题。",
        uploader_claim="普通聊天材料",
        file_name="",
        created_by="10001",
        now=now,
    )
    await repository.update_material_verdict(
        material["id"],
        verdict="match",
        reason="test",
        enabled=True,
        now=now,
    )
    proactive = ProactiveMessageService(
        repository,
        owner_qq="10001",
        bot_qq="20002",
        default_timezone="UTC",
    )
    await proactive.update_policy(
        user_qq="30003",
        enabled=True,
        timezone_name="UTC",
        quiet_hours_enabled=False,
        quiet_start="00:00",
        quiet_end="23:59",
        quiet_behavior=QuietHoursBehavior.DELAY,
        daily_limit=3,
        minimum_interval_seconds=0,
        updated_by="10001",
    )
    gateway = FakeChatGateway()
    search = FakeSearch()
    composer = ProactiveContentComposer(
        repository,
        stats,
        gateway,
        ConversationContextService(PersonaService(repository), MemoryService(repository)),
        search,
        owner_qq="10001",
        bot_qq="20002",
        model_route="test-chat",
        enabled=True,
    )

    assert await composer.run_daily(now) == 0
    assert gateway.calls == 0
    assert search.calls == 0
    assert (await repository.counts())["outbox"] == 0

    await proactive.update_policy(
        user_qq="30003",
        enabled=True,
        timezone_name="UTC",
        quiet_hours_enabled=True,
        quiet_start="00:00",
        quiet_end="23:59",
        quiet_behavior=QuietHoursBehavior.DELAY,
        daily_limit=3,
        minimum_interval_seconds=0,
        auto_content_enabled=True,
        updated_by="10001",
    )
    assert await composer.run_daily(now) == 0
    assert gateway.calls == 0
    assert search.calls == 0
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_material_verifier_requires_exact_match_token(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "material-verdict.sqlite3")
    await repository.initialize()
    gateway = FakeChatGateway("MATCHING\n这个词不应被当成 MATCH。")
    service = MaterialService(
        repository,
        gateway,
        FakeSearch(enabled=False),
        owner_qq="10001",
        bot_qq="20002",
        model_route="test-chat",
        enabled=True,
    )

    material = await service.create(
        title="待核对材料",
        content="正文",
        uploader_claim="说明",
        target_qq="30003",
    )

    assert gateway.calls == 1
    assert material["ai_verdict"] == "mismatch"
    assert not material["enabled"]


@pytest.mark.asyncio
async def test_privacy_delete_covers_partitioned_chat_and_derived_summary(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-partition.sqlite3")
    await repository.initialize()
    payload = {
        "time": 1_700_000_000,
        "self_id": 20002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 991,
        "user_id": 30003,
        "message": [{"type": "text", "data": {"text": "用户发来的私聊"}}],
    }
    message = parse_message_event(payload, expected_bot_qq="20002")
    assert message.conversation_key == "20002:private:30003"
    assert await repository.store_inbound(message)
    outbound = OutboundMessage(
        id=str(uuid4()),
        idempotency_key="privacy-partition:outbound",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="30003",
        segments=(MessageSegment(type="text", data={"text": "机器人回复"}),),
    )
    assert await repository.enqueue_outbound(outbound)
    claimed = await repository.claim_outbound()
    assert claimed is not None
    await repository.mark_outbound_sent(claimed.id, bot_qq="20002")
    run_id = str(uuid4())
    candidate_id = str(uuid4())
    await repository.start_inference_run(
        run_id=run_id,
        source_message_id=message.id,
        conversation_key=message.conversation_key,
        actor_qq="10001",
        bot_qq="20002",
        mode="shadow",
        prompt_hash="privacy-test",
        model_route="test-chat",
    )
    await repository.finish_inference_success(
        run_id=run_id,
        candidate_id=candidate_id,
        source_message_id=message.id,
        conversation_kind="private",
        target_id="30003",
        content="候选回复",
        provider_request_id="fake",
        input_tokens=5,
        output_tokens=2,
        safety_flags=(),
    )
    await repository.save_daily_summary(
        day_key="2026-08-14",
        entries=[{"time": "12:00", "alias": "30003", "text": "私聊摘要"}],
        # Empty provenance emulates a pre-v25 cached summary; alias fallback must remain deletable.
        source_peer_ids=[],
        model_route="fallback",
        now=datetime.now(UTC),
    )

    exported = await repository.privacy_export_snapshot("30003")
    impact = await repository.privacy_delete_impact("30003")
    assert len(exported["messages"]) == 2
    assert [item["id"] for item in exported["inference_runs"]] == [run_id]
    assert exported["daily_summaries"][0]["day_key"] == "2026-08-14"
    assert impact["messages"] == 2
    assert impact["inference_runs"] == 1
    assert impact["reply_candidates"] == 1
    assert impact["daily_summaries"] == 1

    deleted = await repository.delete_user_data("30003", request_id="partition-delete")
    assert deleted["messages"] == 2
    assert await repository.daily_summary("2026-08-14") is None
    counts = await repository.counts()
    assert counts["messages"] == 0
    assert counts["conversations"] == 0
    assert counts["inference_runs"] == 0
    assert counts["reply_candidates"] == 0
