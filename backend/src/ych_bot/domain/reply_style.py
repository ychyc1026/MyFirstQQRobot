"""Per-user spoken reply shape: bubble count range and sentence length."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

MAX_BUBBLES = 10
DEFAULT_MIN_BUBBLES = 1
DEFAULT_MAX_BUBBLES = 1
DEFAULT_SENTENCE_MIN_CHARS = 12
DEFAULT_SENTENCE_MAX_CHARS = 80

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…])")
_SOFT_SPLIT = re.compile(r"(?<=[，、,；;])")


@dataclass(frozen=True, slots=True)
class UserReplyStylePolicy:
    user_qq: str
    min_bubbles: int = DEFAULT_MIN_BUBBLES
    max_bubbles: int = DEFAULT_MAX_BUBBLES
    sentence_min_chars: int = DEFAULT_SENTENCE_MIN_CHARS
    sentence_max_chars: int = DEFAULT_SENTENCE_MAX_CHARS

    def validate(self) -> None:
        if not self.user_qq.isdigit() or not (5 <= len(self.user_qq) <= 20):
            raise ValueError("user_qq must contain 5 to 20 digits")
        if not (1 <= self.min_bubbles <= self.max_bubbles <= MAX_BUBBLES):
            raise ValueError("bubble range must be 1 to 10, and min cannot exceed max")
        if not (4 <= self.sentence_min_chars <= self.sentence_max_chars <= 200):
            raise ValueError("sentence length range must be 4 to 200, and min cannot exceed max")

    def prompt_directive(self, bubble_count: int) -> str:
        return (
            "这是对用户这一句的一次完整回复，不要另起话题。"
            f"写成大约 {bubble_count} 句口语短句，每句大约 "
            f"{self.sentence_min_chars} 到 {self.sentence_max_chars} 个字。"
            "句与句连起来应是一段自然的话，不要编号、不要分点、不要说自己在分段。"
            "后端会把这几句拆成多条 QQ 消息框发送。"
        )


def group_vision_reply_style(user_qq: str) -> UserReplyStylePolicy:
    return UserReplyStylePolicy(
        user_qq=user_qq,
        min_bubbles=1,
        max_bubbles=2,
        sentence_min_chars=4,
        sentence_max_chars=80,
    )


def policy_for_reply_run(
    *,
    user_qq: str,
    conversation_kind: str,
    stored_policy: dict[str, object] | None,
    safety_flags: tuple[str, ...] | list[str] = (),
) -> UserReplyStylePolicy:
    flags = tuple(safety_flags)
    if conversation_kind != "private" and "vision_reply" in flags:
        return group_vision_reply_style(user_qq)
    if conversation_kind == "private":
        return policy_from_record(user_qq, stored_policy)
    return UserReplyStylePolicy(user_qq=user_qq)


def policy_from_record(user_qq: str, row: dict[str, object] | None) -> UserReplyStylePolicy:
    if row is None:
        return UserReplyStylePolicy(user_qq=user_qq)
    return UserReplyStylePolicy(
        user_qq=user_qq,
        min_bubbles=int(row["min_bubbles"]),
        max_bubbles=int(row["max_bubbles"]),
        sentence_min_chars=int(row["sentence_min_chars"]),
        sentence_max_chars=int(row["sentence_max_chars"]),
    )


def choose_bubble_count(
    policy: UserReplyStylePolicy,
    rng: random.Random | None = None,
) -> int:
    policy.validate()
    picker = rng or random.Random()
    return picker.randint(policy.min_bubbles, policy.max_bubbles)


def split_into_bubbles(
    text: str,
    *,
    count: int,
    min_chars: int,
    max_chars: int,
) -> tuple[str, ...]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ()
    target = max(1, min(int(count), MAX_BUBBLES))
    parts = [item.strip() for item in _SENTENCE_SPLIT.split(cleaned) if item.strip()]
    if len(parts) <= 1:
        parts = [item.strip() for item in _SOFT_SPLIT.split(cleaned) if item.strip()] or [cleaned]
    return tuple(_rebalance(parts, target, min_chars, max_chars))


def _rebalance(
    parts: list[str],
    target: int,
    min_chars: int,
    max_chars: int,
) -> list[str]:
    while len(parts) > target:
        index = min(range(len(parts) - 1), key=lambda i: len(parts[i]) + len(parts[i + 1]))
        parts[index] = f"{parts[index]}{parts[index + 1]}"
        del parts[index + 1]
    guard = 0
    while len(parts) < target and guard < 20:
        guard += 1
        longest = max(range(len(parts)), key=lambda i: len(parts[i]))
        if len(parts[longest]) < max(min_chars * 2, 8):
            break
        left, right = _split_once(parts[longest])
        if not right:
            break
        parts[longest] = left
        parts.insert(longest + 1, right)
    wrapped: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            wrapped.append(part)
            continue
        wrapped.extend(_hard_wrap(part, max_chars))
    if len(wrapped) > MAX_BUBBLES:
        head = wrapped[: MAX_BUBBLES - 1]
        head.append("".join(wrapped[MAX_BUBBLES - 1 :]))
        return head
    return wrapped


def _split_once(text: str) -> tuple[str, str]:
    mid = max(1, len(text) // 2)
    for pattern in ("。", "！", "？", "，", "、", ",", ";", " "):
        index = text.rfind(pattern, 0, mid + 4)
        if index >= max(1, mid // 3):
            return text[: index + 1].strip(), text[index + 1 :].strip()
    return text[:mid].strip(), text[mid:].strip()


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    size = max(4, max_chars)
    return [text[index : index + size] for index in range(0, len(text), size)]
