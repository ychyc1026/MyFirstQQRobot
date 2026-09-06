from __future__ import annotations

import pytest
from ych_bot.domain.reply_planning import ReplyPlanningError, build_reply_plan
from ych_bot.domain.reply_style import UserReplyStylePolicy


def policy(
    *,
    min_bubbles: int = 1,
    max_bubbles: int = 3,
    min_chars: int = 4,
    max_chars: int = 40,
) -> UserReplyStylePolicy:
    return UserReplyStylePolicy(
        user_qq="123456789",
        min_bubbles=min_bubbles,
        max_bubbles=max_bubbles,
        sentence_min_chars=min_chars,
        sentence_max_chars=max_chars,
    )


def test_reply_plan_is_deterministic_and_has_stable_idempotency_keys() -> None:
    content = "今天挺好的。刚刚把手头的事情做完了。你今天过得怎么样？"

    first = build_reply_plan(run_id="run-stable", content=content, policy=policy())
    second = build_reply_plan(run_id="run-stable", content=content, policy=policy())

    assert first == second
    assert [item.sequence for item in first.bubbles] == list(range(1, len(first.bubbles) + 1))
    assert [item.idempotency_key for item in first.bubbles] == [
        f"run-stable:bubble:{index}" for index in range(1, len(first.bubbles) + 1)
    ]


def test_reply_plan_preserves_complete_model_text() -> None:
    content = "先休息一下。等精神好一点，再继续做也来得及。"
    plan = build_reply_plan(
        run_id="run-complete",
        content=content,
        policy=policy(min_bubbles=2, max_bubbles=2),
    )

    texts = [str(item.segments[0].data["text"]) for item in plan.bubbles]
    assert len(texts) == 2
    assert "".join(texts) == content
    assert all(len(text) <= 40 for text in texts)


def test_reply_plan_rejects_text_that_cannot_fit_without_truncation() -> None:
    with pytest.raises(ReplyPlanningError, match="capacity"):
        build_reply_plan(
            run_id="run-too-long",
            content="长" * 81,
            policy=policy(min_bubbles=1, max_bubbles=2, max_chars=40),
        )


def test_group_vision_style_fits_a_short_caption_that_exceeds_one_bubble() -> None:
    from ych_bot.domain.reply_style import policy_for_reply_run

    policy = policy_for_reply_run(
        user_qq="2000000001",
        conversation_kind="group",
        stored_policy=None,
        safety_flags=("not_for_delivery", "vision_reply"),
    )
    content = "短" * 103
    plan = build_reply_plan(run_id="run-group-vision", content=content, policy=policy)
    assert 1 <= len(plan.bubbles) <= 2
    assert "".join(str(item.segments[0].data["text"]) for item in plan.bubbles) == content


def test_group_text_style_stays_one_bubble() -> None:
    from ych_bot.domain.reply_style import policy_for_reply_run

    policy = policy_for_reply_run(
        user_qq="2000000001",
        conversation_kind="group",
        stored_policy=None,
        safety_flags=("not_for_delivery",),
    )
    assert policy.max_bubbles == 1
    assert policy.sentence_max_chars == 80


def test_reply_plan_rejects_empty_candidate() -> None:
    with pytest.raises(ReplyPlanningError, match="empty"):
        build_reply_plan(run_id="run-empty", content=" \n ", policy=policy())
