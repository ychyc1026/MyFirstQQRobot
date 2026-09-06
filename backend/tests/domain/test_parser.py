from ych_bot.domain.errors import IgnoredEvent
from ych_bot.domain.models import ConversationKind
from ych_bot.infrastructure.napcat.parser import parse_message_event

BOT_QQ = "2000000002"


def private_event(message_id: int = 101) -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": 123456789,
        "message": [
            {"type": "reply", "data": {"id": "99"}},
            {"type": "text", "data": {"text": "你好 YCH"}},
            {"type": "image", "data": {"file": "demo.jpg"}},
        ],
        "raw_message": "你好 YCH",
    }


def test_private_message_preserves_segments_and_string_ids() -> None:
    message = parse_message_event(private_event(), expected_bot_qq=BOT_QQ)

    assert message.bot_qq == BOT_QQ
    assert message.sender_id == "123456789"
    assert message.conversation_kind is ConversationKind.PRIVATE
    assert message.conversation_key == f"{BOT_QQ}:private:123456789"
    assert message.source_message_id == "101"
    assert message.reply_to_message_id == "99"
    assert message.plain_text == "你好 YCH"
    assert message.vision_image_urls == ()
    assert [segment.type for segment in message.segments] == ["reply", "text", "image"]


def test_group_message_uses_group_conversation() -> None:
    payload = private_event()
    payload.update({"message_type": "group", "group_id": 987654321})

    message = parse_message_event(payload, expected_bot_qq=BOT_QQ)

    assert message.conversation_kind is ConversationKind.GROUP
    assert message.conversation_id == "987654321"
    assert message.conversation_key == f"{BOT_QQ}:group:987654321"
    assert message.mentions_bot is False


def test_group_at_bot_is_detected_without_entering_plain_text() -> None:
    payload = private_event()
    payload.update({"message_type": "group", "group_id": 987654321})
    payload["message"] = [
        {"type": "at", "data": {"qq": BOT_QQ}},
        {"type": "text", "data": {"text": "在吗"}},
    ]

    message = parse_message_event(payload, expected_bot_qq=BOT_QQ)

    assert message.mentions_bot is True
    assert message.mentioned_qq_ids == (BOT_QQ,)
    assert message.plain_text == "在吗"


def test_foreign_bot_event_is_ignored() -> None:
    payload = private_event()
    payload["self_id"] = 2000000001

    try:
        parse_message_event(payload, expected_bot_qq=BOT_QQ)
    except IgnoredEvent as error:
        assert error.reason == "foreign_bot_event"
    else:
        raise AssertionError("foreign bot event should have been ignored")


def test_self_message_is_ignored() -> None:
    payload = private_event()
    payload["user_id"] = int(BOT_QQ)

    try:
        parse_message_event(payload, expected_bot_qq=BOT_QQ)
    except IgnoredEvent as error:
        assert error.reason == "self_message"
    else:
        raise AssertionError("self message should have been ignored")
