from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.domain.identity import (
    FriendState,
    HistoryAccessMode,
    HistoryPolicy,
    decide_friend_state,
)
from ych_bot.infrastructure.database import SQLiteRepository


def test_first_message_alone_never_marks_user_as_new_friend() -> None:
    decision = decide_friend_state()

    assert decision.state is FriendState.UNKNOWN
    assert decision.reason == "insufficient_evidence"


def test_history_policy_denies_live_reads_by_default() -> None:
    policy = HistoryPolicy()

    assert policy.mode is HistoryAccessMode.DENY
    assert policy.allows_live_history_read is False


@pytest.mark.asyncio
async def test_baseline_and_friend_add_produce_evidence_based_states(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "identity.sqlite3")
    await repository.initialize()
    baseline_time = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)

    await repository.record_friend_baseline(
        bot_qq="2000000002",
        friend_ids=["10001", "10002"],
        captured_at=baseline_time,
    )
    state, inserted = await repository.record_friend_add(
        bot_qq="2000000002",
        user_qq="10003",
        occurred_at=baseline_time + timedelta(minutes=5),
        event_key="friend-add:10003:1",
        raw_event={"post_type": "notice", "notice_type": "friend_add"},
    )

    assert inserted is True
    assert state is FriendState.NEW_FRIEND
    summary = await repository.identity_summary()
    assert summary["friend_states"]["existing_friend"] == 2
    assert summary["friend_states"]["new_friend"] == 1
    assert summary["history_modes"]["deny"] == 3


@pytest.mark.asyncio
async def test_friend_add_without_baseline_remains_unknown(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "identity.sqlite3")
    await repository.initialize()

    state, inserted = await repository.record_friend_add(
        bot_qq="2000000002",
        user_qq="10004",
        occurred_at=datetime(2026, 8, 12, 8, 0, tzinfo=UTC),
        event_key="friend-add:10004:1",
        raw_event={"post_type": "notice", "notice_type": "friend_add"},
    )

    assert inserted is True
    assert state is FriendState.UNKNOWN
    summary = await repository.identity_summary()
    assert summary["friend_states"]["unknown"] == 1
    assert summary["history_modes"]["deny"] == 1


@pytest.mark.asyncio
async def test_owner_override_can_return_to_latest_strong_evidence(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "identity-restore.sqlite3")
    await repository.initialize()
    baseline_time = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    await repository.record_friend_baseline(
        bot_qq="2000000002",
        friend_ids=["10001"],
        captured_at=baseline_time,
    )
    await repository.set_friend_state(
        user_qq="10001",
        state=FriendState.NOT_FRIEND,
        updated_by="2000000001",
        reason="test override",
    )

    restored = await repository.restore_evidence_friend_state(
        user_qq="10001",
        updated_by="2000000001",
        reason="test release",
    )

    assert restored["friend_state"] == FriendState.EXISTING_FRIEND.value
    assert restored["state_source"] == "baseline_snapshot"
    detail = await repository.user_detail("10001")
    assert detail["relationship"]["owner_overridden_by"] is None
    assert detail["identity_evidence"][0]["evidence_type"] == "owner_override_released"


@pytest.mark.asyncio
async def test_restore_without_strong_evidence_returns_to_unknown(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "identity-restore-unknown.sqlite3")
    await repository.initialize()
    await repository.set_friend_state(
        user_qq="10002",
        state=FriendState.NEW_FRIEND,
        updated_by="2000000001",
        reason="test override",
    )

    restored = await repository.restore_evidence_friend_state(
        user_qq="10002",
        updated_by="2000000001",
        reason="test release",
    )

    assert restored["friend_state"] == FriendState.UNKNOWN.value
    assert restored["state_source"] == "insufficient_evidence"
