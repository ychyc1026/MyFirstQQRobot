import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings
from ych_bot.domain.reply_style import (
    UserReplyStylePolicy,
    choose_bubble_count,
    split_into_bubbles,
)
from ych_bot.infrastructure.database import SQLiteRepository


def test_choose_bubble_count_stays_inside_range() -> None:
    policy = UserReplyStylePolicy(user_qq="123456789", min_bubbles=2, max_bubbles=4)
    rng = random.Random(3)
    chosen = {choose_bubble_count(policy, rng) for _ in range(20)}
    assert chosen <= {2, 3, 4}
    assert len(chosen) >= 2


def test_split_into_bubbles_keeps_one_coherent_reply() -> None:
    text = "今天天气不错。要不要出门走走？我请你喝奶茶。"
    bubbles = split_into_bubbles(text, count=3, min_chars=6, max_chars=40)
    assert 2 <= len(bubbles) <= 3
    assert "".join(bubbles).replace(" ", "") == text.replace(" ", "")


def test_split_into_bubbles_never_exceeds_hard_cap() -> None:
    text = "啊。" * 40
    bubbles = split_into_bubbles(text, count=12, min_chars=4, max_chars=8)
    assert 1 <= len(bubbles) <= 10


def test_reply_style_policy_rejects_invalid_range() -> None:
    policy = UserReplyStylePolicy(user_qq="123456789", min_bubbles=4, max_bubbles=2)
    try:
        policy.validate()
    except ValueError as error:
        assert "min cannot exceed max" in str(error)
    else:
        raise AssertionError("invalid bubble range should fail")


@pytest.mark.asyncio
async def test_privacy_export_and_delete_include_reply_style(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-style-privacy.sqlite3")
    await repository.initialize()
    policy = UserReplyStylePolicy(
        user_qq="123456789",
        min_bubbles=1,
        max_bubbles=3,
        sentence_min_chars=10,
        sentence_max_chars=60,
    )
    from datetime import UTC, datetime

    await repository.upsert_reply_style_policy(
        policy,
        updated_by="2000000001",
        now=datetime.now(UTC),
    )
    exported = await repository.privacy_export_snapshot("123456789")
    assert exported["user_reply_style_policy"][0]["max_bubbles"] == 3
    impact = await repository.privacy_delete_impact("123456789")
    assert impact["user_reply_style_policies"] == 1
    await repository.delete_user_data("123456789", request_id="reply-style-delete")
    assert await repository.reply_style_policy("123456789") is None


def test_dashboard_can_get_and_update_user_reply_style(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "reply-style-ui.sqlite3",
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
        default = client.get("/api/v1/users/30003/reply-style", headers=headers)
        assert default.status_code == 200
        assert default.json()["min_bubbles"] == 1
        assert default.json()["max_bubbles"] == 1
        assert default.json()["persisted"] is False
        assert default.json()["outbound_attached"] is False
        updated = client.put(
            "/api/v1/users/30003/reply-style",
            headers=headers,
            json={
                "min_bubbles": 1,
                "max_bubbles": 4,
                "sentence_min_chars": 12,
                "sentence_max_chars": 48,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["max_bubbles"] == 4
        assert updated.json()["persisted"] is True
        detail = client.get("/api/v1/users/30003", headers=headers)
        assert detail.json()["reply_style"]["max_bubbles"] == 4
        rejected = client.put(
            "/api/v1/users/30003/reply-style",
            headers=headers,
            json={
                "min_bubbles": 6,
                "max_bubbles": 2,
                "sentence_min_chars": 12,
                "sentence_max_chars": 48,
            },
        )
        assert rejected.status_code == 422
