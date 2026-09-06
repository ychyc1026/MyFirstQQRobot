"""Owner-authority helpers: fixed identities, command construction, NapCat fakes."""

from __future__ import annotations

from uuid import uuid4

import httpx
from ych_bot.domain.control import (
    ControlSource,
    OwnerCommand,
    OwnerCommandKind,
    parse_owner_command,
)
from ych_bot.infrastructure.napcat.parser import parse_message_event

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"


def private_message(text: str, *, sender: str = OWNER_QQ) -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": 909,
        "user_id": int(sender),
        "message": [{"type": "text", "data": {"text": text}}],
    }


def parse_owner_text(text: str, *, sender: str = OWNER_QQ) -> OwnerCommand | None:
    message = parse_message_event(private_message(text, sender=sender), expected_bot_qq=BOT_QQ)
    return parse_owner_command(message, owner_qq=OWNER_QQ)


def owner_command(
    kind: OwnerCommandKind,
    arguments: dict[str, str] | None = None,
) -> OwnerCommand:
    command_id = str(uuid4())
    return OwnerCommand(
        id=command_id,
        kind=kind,
        actor_qq=OWNER_QQ,
        source_message_id=f"test:{command_id}",
        arguments=arguments or {},
        source=ControlSource.DASHBOARD,
    )


def friend_list_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/get_friend_list"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "retcode": 0,
                "data": [{"user_id": 10001}, {"user_id": "10002"}],
            },
        )

    return httpx.MockTransport(handler)
