"""Deterministic normalization of model text into bounded reply bubbles."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .models import MessageSegment
from .reply_pipeline import ReplyBubble, ReplyPlan
from .reply_style import UserReplyStylePolicy, split_into_bubbles


class ReplyPlanningError(ValueError):
    """Model text cannot be converted into a safe, complete reply plan."""


def build_reply_plan(
    *,
    run_id: str,
    content: str,
    policy: UserReplyStylePolicy,
) -> ReplyPlan:
    policy.validate()
    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        raise ReplyPlanningError("reply candidate is empty")
    capacity = policy.max_bubbles * policy.sentence_max_chars
    if len(normalized) > capacity:
        raise ReplyPlanningError("reply candidate exceeds configured bubble capacity")
    needed = max(1, (len(normalized) + policy.sentence_max_chars - 1) // policy.sentence_max_chars)
    count = max(_stable_bubble_count(run_id, normalized, policy), needed)
    texts = split_into_bubbles(
        normalized,
        count=count,
        min_chars=policy.sentence_min_chars,
        max_chars=policy.sentence_max_chars,
    )
    if not texts or not (policy.min_bubbles <= len(texts) <= policy.max_bubbles):
        raise ReplyPlanningError("reply candidate produced an invalid bubble count")
    if any(not text.strip() or len(text) > policy.sentence_max_chars for text in texts):
        raise ReplyPlanningError("reply candidate produced an invalid bubble length")
    if "".join(texts).replace(" ", "") != normalized.replace(" ", ""):
        raise ReplyPlanningError("reply bubble planning changed the candidate meaning")
    bubbles = tuple(
        ReplyBubble(
            sequence=index,
            idempotency_key=f"{run_id}:bubble:{index}",
            segments=(MessageSegment("text", {"text": text}),),
        )
        for index, text in enumerate(texts, start=1)
    )
    return ReplyPlan(run_id=run_id, bubbles=bubbles)


def restore_reply_plan(payload: dict[str, Any]) -> ReplyPlan:
    """Restore and revalidate a plan from durable JSON."""

    return ReplyPlan(
        run_id=str(payload["run_id"]),
        bubbles=tuple(
            ReplyBubble(
                sequence=int(item["sequence"]),
                idempotency_key=str(item["idempotency_key"]),
                segments=tuple(
                    MessageSegment(str(segment["type"]), dict(segment["data"]))
                    for segment in item["segments"]
                ),
            )
            for item in payload["bubbles"]
        ),
    )


def _stable_bubble_count(
    run_id: str,
    content: str,
    policy: UserReplyStylePolicy,
) -> int:
    span = policy.max_bubbles - policy.min_bubbles + 1
    digest = hashlib.sha256(f"{run_id}\n{content}".encode()).digest()
    return policy.min_bubbles + int.from_bytes(digest[:8], "big") % span
