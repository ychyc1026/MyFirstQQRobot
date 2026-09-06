"""Authorized, time-limited QQ Zone profile reads that only create candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ych_bot.domain.memory import MemoryKind, MemorySource
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatModelGateway,
    ModelMessage,
    ModelRole,
)
from ych_bot.domain.models import (
    ConversationKind,
    MessageSegment,
    OutboundMessage,
    safe_model_image_url,
)
from ych_bot.domain.readiness import CapabilityScope
from ych_bot.infrastructure.database import SQLiteRepository

from .inbound_images import ImageInlineError, ImageInliner, InboundImageRef
from .readiness_guard import ReadinessBlockedError, ReadinessGuard

DEFAULT_CANDIDATE_TTL_DAYS = 7
MAX_PROFILE_ITEMS = 20
DEFAULT_PROFILE_ITEMS = 10
MAX_VISION_IMAGES = 3
_VISION_STANCE = (
    "这是对方空间里的一张图或视频封面。用一句人话记下对理解这个人有用的内容。"
    "不要解说图，不要分点。如果这是视频封面，说明你只看到封面。"
)


class QzoneProfileClient(Protocol):
    async def get_qzone_msg_list(self, *, user_qq: str, count: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class QzoneProfilePreviewResult:
    allowed: bool
    reason: str
    snapshot: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    policy: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.public_dict()

    def public_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "summary": self.summary,
            "policy": self.policy,
        }


class QzoneProfileService:
    def __init__(
        self,
        repository: SQLiteRepository,
        client: QzoneProfileClient,
        *,
        owner_qq: str,
        readiness_guard: ReadinessGuard,
        collection_enabled: bool = False,
        bot_qq: str = "",
        clock: Callable[[], datetime] | None = None,
        candidate_ttl_days: int = DEFAULT_CANDIDATE_TTL_DAYS,
        vision_gateway: ChatModelGateway | None = None,
        vision_model_route: str = "",
        image_inliner: ImageInliner | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._owner_qq = owner_qq
        self._bot_qq = bot_qq
        self._readiness_guard = readiness_guard
        self._collection_enabled = collection_enabled
        self._clock = clock or (lambda: datetime.now(UTC))
        self._candidate_ttl_days = candidate_ttl_days
        self._vision_gateway = vision_gateway
        self._vision_model_route = vision_model_route
        self._image_inliner = image_inliner

    async def preview(
        self,
        user_qq: str,
        *,
        purpose: str,
        actor_qq: str,
        source: str = "command",
    ) -> QzoneProfilePreviewResult:
        normalized_purpose = purpose.strip() or "initialize_user_profile"
        now = self._clock()
        policy = await self._repository.qzone_profile_policy(user_qq)
        requested_count = _requested_count(policy.get("max_items"))
        authorization = await self._repository.authorize_qzone_profile_access(
            user_qq=user_qq,
            requested_count=requested_count,
            purpose=normalized_purpose,
            accessor=actor_qq,
            collection_enabled=self._collection_enabled,
            now=now,
            consume=False,
        )
        if not authorization["allowed"]:
            result = QzoneProfilePreviewResult(
                allowed=False,
                reason=authorization["reason"],
                policy=policy,
                summary=_empty_summary(authorization["reason"]),
            )
            await self._ack_owner(user_qq, result, source=source, now=now)
            return result

        try:
            await self._readiness_guard.require(
                CapabilityScope.QZONE_PROFILE,
                operation="qzone.profile_read",
                operation_id=f"{user_qq}:{normalized_purpose}",
            )
        except ReadinessBlockedError as exc:
            result = QzoneProfilePreviewResult(
                allowed=False,
                reason=f"readiness_{exc.blocker_codes[0]}",
                policy=policy,
                summary=_empty_summary(f"readiness_{exc.blocker_codes[0]}"),
            )
            await self._ack_owner(user_qq, result, source=source, now=now)
            return result
        authorization = await self._repository.authorize_qzone_profile_access(
            user_qq=user_qq,
            requested_count=requested_count,
            purpose=normalized_purpose,
            accessor=actor_qq,
            collection_enabled=self._collection_enabled,
            now=now,
            consume=True,
        )
        if not authorization["allowed"]:
            result = QzoneProfilePreviewResult(
                allowed=False,
                reason=authorization["reason"],
                policy=await self._repository.qzone_profile_policy(user_qq),
                summary=_empty_summary(authorization["reason"]),
            )
            await self._ack_owner(user_qq, result, source=source, now=now)
            return result
        payload = await self._client.get_qzone_msg_list(
            user_qq=user_qq,
            count=requested_count,
        )
        expires_at = now + timedelta(days=self._candidate_ttl_days)
        snapshot_id = str(uuid4())
        stored_payload, vision_jobs = _normalized_payload(payload)
        summary = stored_payload["summary"]
        snapshot = await self._repository.store_qzone_profile_snapshot(
            snapshot_id=snapshot_id,
            user_qq=user_qq,
            purpose=normalized_purpose,
            source="napcat.get_qzone_msg_list",
            payload=stored_payload,
            fetched_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            created_by=actor_qq,
        )
        candidates = await self._create_candidates(
            user_qq=user_qq,
            snapshot_id=snapshot_id,
            snapshot=snapshot,
            created_by=actor_qq,
            expires_at=expires_at.isoformat(),
        )
        vision_candidates, vision_counts = await self._vision_candidates(
            user_qq=user_qq,
            snapshot_id=snapshot_id,
            jobs=vision_jobs,
            created_by=actor_qq,
            expires_at=expires_at.isoformat(),
        )
        candidates.extend(vision_candidates)
        summary = {
            **summary,
            "images_seen": vision_counts["images_seen"],
            "covers_seen": vision_counts["covers_seen"],
            "candidate_count": len(candidates),
        }
        snapshot = {**snapshot, "summary": summary}
        result = QzoneProfilePreviewResult(
            allowed=True,
            reason="authorized",
            snapshot=snapshot,
            candidates=tuple(candidates),
            policy=await self._repository.qzone_profile_policy(user_qq),
            summary=summary,
        )
        await self._ack_owner(user_qq, result, source=source, now=now)
        return result

    async def _create_candidates(
        self,
        *,
        user_qq: str,
        snapshot_id: str,
        snapshot: dict[str, Any],
        created_by: str,
        expires_at: str,
    ) -> list[dict[str, Any]]:
        drafts: list[tuple[MemoryKind, str, dict[str, Any]]] = []
        nickname = snapshot.get("nickname")
        if isinstance(nickname, str) and nickname.strip():
            drafts.append(
                (
                    MemoryKind.FACT,
                    "qzone.nickname",
                    {"text": nickname.strip(), "snapshot_id": snapshot_id},
                )
            )
        signature = snapshot.get("signature")
        if isinstance(signature, str) and signature.strip():
            drafts.append(
                (
                    MemoryKind.PREFERENCE,
                    "qzone.signature",
                    {"text": signature.strip(), "snapshot_id": snapshot_id},
                )
            )
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            tid = str(item.get("tid") or "").strip()
            key = f"qzone.post.{tid}" if tid else f"qzone.post.{uuid4().hex[:12]}"
            if content:
                drafts.append(
                    (
                        MemoryKind.RELATIONSHIP_EVENT,
                        key,
                        {
                            "text": content,
                            "tid": tid or None,
                            "published_at": item.get("published_at"),
                            "snapshot_id": snapshot_id,
                        },
                    )
                )
                continue
            if item.get("unread_video"):
                drafts.append(
                    (
                        MemoryKind.RELATIONSHIP_EVENT,
                        key,
                        {
                            "text": "发了视频，内容没读到",
                            "tid": tid or None,
                            "media": "video_unread",
                            "snapshot_id": snapshot_id,
                        },
                    )
                )
        return await self._store_drafts(
            user_qq=user_qq,
            drafts=drafts,
            created_by=created_by,
            snapshot_id=snapshot_id,
            expires_at=expires_at,
        )

    async def _vision_candidates(
        self,
        *,
        user_qq: str,
        snapshot_id: str,
        jobs: tuple[dict[str, Any], ...],
        created_by: str,
        expires_at: str,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        counts = {"images_seen": 0, "covers_seen": 0}
        if not jobs or self._vision_gateway is None or self._image_inliner is None:
            return [], counts
        drafts: list[tuple[MemoryKind, str, dict[str, Any]]] = []
        for job in jobs[:MAX_VISION_IMAGES]:
            url = safe_model_image_url(job.get("url"))
            if not url:
                continue
            try:
                inlined = await self._image_inliner((InboundImageRef(url=url),))
            except ImageInlineError:
                continue
            request = ChatGenerationRequest(
                request_id=f"qzone-profile:{snapshot_id}:{job['key']}",
                messages=(
                    ModelMessage(role=ModelRole.SYSTEM, content=_VISION_STANCE),
                    ModelMessage(
                        role=ModelRole.USER,
                        content="（刚甩过来一张图）"
                        if not job.get("cover")
                        else "（只看到视频封面）",
                        image_urls=inlined,
                    ),
                ),
                max_output_tokens=80,
            )
            try:
                generated = await self._vision_gateway.generate(request)
            except Exception:
                continue
            text = str(getattr(generated, "text", "") or "").strip()
            if not text:
                continue
            if job.get("cover"):
                counts["covers_seen"] += 1
            else:
                counts["images_seen"] += 1
            drafts.append(
                (
                    MemoryKind.RELATIONSHIP_EVENT,
                    job["key"],
                    {
                        "text": text,
                        "media": "cover_only" if job.get("cover") else "image",
                        "snapshot_id": snapshot_id,
                    },
                )
            )
        stored = await self._store_drafts(
            user_qq=user_qq,
            drafts=drafts,
            created_by=created_by,
            snapshot_id=snapshot_id,
            expires_at=expires_at,
        )
        return stored, counts

    async def _store_drafts(
        self,
        *,
        user_qq: str,
        drafts: list[tuple[MemoryKind, str, dict[str, Any]]],
        created_by: str,
        snapshot_id: str,
        expires_at: str,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for kind, key, value in drafts:
            created = await self._repository.create_memory(
                memory_id=str(uuid4()),
                user_qq=user_qq,
                kind=kind,
                key=key,
                value=value,
                source=MemorySource.QZONE_DERIVED,
                confidence=0.4,
                created_by=created_by,
                source_id=snapshot_id,
                expires_at=expires_at,
            )
            candidates.append(
                {
                    "id": created["id"],
                    "status": created["status"],
                    "source": MemorySource.QZONE_DERIVED.value,
                    "expires_at": expires_at,
                    "key": key,
                    "value": value,
                }
            )
        return candidates

    async def _ack_owner(
        self,
        user_qq: str,
        result: QzoneProfilePreviewResult,
        *,
        source: str,
        now: datetime,
    ) -> None:
        summary = result.summary or _empty_summary(result.reason)
        entry = "仪表盘" if source == "dashboard" else "主号指令"
        outcome = "已读取" if result.allowed else f"未读取（{result.reason}）"
        text = (
            f"空间资料 {user_qq}\n"
            f"入口：{entry}\n"
            f"结果：{outcome}\n"
            f"说说 {int(summary.get('scanned') or 0)} 条，"
            f"候选 {int(summary.get('candidate_count') or 0)} 条\n"
            f"识图 {int(summary.get('images_seen') or 0)} 张，"
            f"封面 {int(summary.get('covers_seen') or 0)} 条，"
            f"视频未读 {int(summary.get('videos_unread') or 0)} 条"
        )
        await self._repository.enqueue_outbound(
            OutboundMessage(
                id=str(uuid4()),
                idempotency_key=f"qzone-profile-ack:{user_qq}:{now.isoformat()}:{source}",
                conversation_kind=ConversationKind.PRIVATE,
                target_id=self._owner_qq,
                segments=(MessageSegment(type="text", data={"text": text}),),
            )
        )


def _empty_summary(reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "scanned": 0,
        "images_seen": 0,
        "covers_seen": 0,
        "videos_unread": 0,
        "candidate_count": 0,
    }


def _requested_count(max_items: object) -> int:
    if max_items in (None, ""):
        return DEFAULT_PROFILE_ITEMS
    try:
        value = int(max_items)
    except (TypeError, ValueError):
        return DEFAULT_PROFILE_ITEMS
    return max(1, min(value, MAX_PROFILE_ITEMS))


def _normalized_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    items: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    videos_unread = 0
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        classified = _classify_item(raw)
        persist = classified["persist"]
        job = classified["job"]
        items.append(persist)
        if persist.get("unread_video"):
            videos_unread += 1
        if job is not None and len(jobs) < MAX_VISION_IMAGES:
            jobs.append(job)
    nickname = payload.get("nickname")
    signature = payload.get("signature")
    stored = {
        "nickname": str(nickname).strip() if nickname else None,
        "signature": str(signature).strip() if signature else None,
        "items": items,
        "summary": {
            "scanned": len(items),
            "images_seen": 0,
            "covers_seen": 0,
            "videos_unread": videos_unread,
            "candidate_count": 0,
        },
    }
    return stored, tuple(jobs)


def _classify_item(item: dict[str, Any]) -> dict[str, Any]:
    tid = str(item.get("tid") or "").strip() or None
    content = str(item.get("content") or "").strip()
    published_at = item.get("published_at")
    has_video = bool(
        item.get("has_video") or item.get("video") or item.get("video_url") or item.get("videoUrl")
    )
    cover = (
        safe_model_image_url(item.get("cover_url"))
        or safe_model_image_url(item.get("cover"))
        or safe_model_image_url(item.get("thumb"))
    )
    image_url = _first_image_url(item)
    unread_video = bool(has_video and not cover)
    persist = {
        "tid": tid,
        "content": content,
        "published_at": published_at,
        "media_kind": (
            "video" if has_video else "image" if image_url else "text" if content else "unknown"
        ),
        "unread_video": unread_video,
        "has_cover": bool(has_video and cover),
    }
    job = None
    key = f"qzone.post.{tid}" if tid else f"qzone.media.{uuid4().hex[:12]}"
    if cover and has_video:
        job = {"key": f"{key}.cover", "url": cover, "cover": True}
    elif image_url and not has_video:
        job = {"key": f"{key}.image", "url": image_url, "cover": False}
    return {"persist": persist, "job": job}


def _first_image_url(item: dict[str, Any]) -> str | None:
    direct = safe_model_image_url(item.get("image_url") or item.get("url"))
    if direct:
        return direct
    for key in ("images", "pics", "pic", "picture"):
        raw = item.get(key)
        if isinstance(raw, str):
            found = safe_model_image_url(raw)
            if found:
                return found
        if isinstance(raw, dict):
            found = safe_model_image_url(raw.get("url") or raw.get("image_url"))
            if found:
                return found
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    found = safe_model_image_url(entry)
                    if found:
                        return found
                if isinstance(entry, dict):
                    found = safe_model_image_url(entry.get("url") or entry.get("image_url"))
                    if found:
                        return found
    return None
