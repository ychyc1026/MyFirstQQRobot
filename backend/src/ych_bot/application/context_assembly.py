"""Build provenance-aware reply context without rendering a model prompt."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.context_budget import ContextBudgetError, apply_context_budget
from ych_bot.domain.context_isolation import (
    ContextIsolationError,
    validate_context_isolation,
)
from ych_bot.domain.models import ConversationKind
from ych_bot.domain.persona import ProfileScope, ProfileSource
from ych_bot.domain.reply_pipeline import (
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    ReplyRunStage,
)

from .context import ConversationContextService


class ContextAssemblyError(RuntimeError):
    """A reply run cannot safely assemble context."""


class ProvenanceContextAssembler:
    def __init__(self, repository: Any, context_service: ConversationContextService) -> None:
        self._repository = repository
        self._context_service = context_service

    async def assemble(
        self,
        *,
        run_id: str,
        lease_token: str,
        actor_qq: str,
        owner_qq: str,
        bot_qq: str,
        budget_chars: int = 12_000,
        now: datetime | None = None,
    ) -> ContextManifest:
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        run = await self._repository.reply_run_detail(run_id)
        if run is None:
            raise ContextAssemblyError("reply run not found")
        if run["stage"] != ReplyRunStage.ASSEMBLING_CONTEXT.value:
            raise ContextAssemblyError("reply run is not assembling context")
        lease = run["lease"]
        if not lease or lease["lease_token"] != lease_token:
            raise ContextAssemblyError("reply run lease does not match")
        if datetime.fromisoformat(lease["expires_at"]).astimezone(UTC) <= current_time:
            raise ContextAssemblyError("reply run lease has expired")

        kind = ConversationKind(run["conversation_kind"])
        peer_id = str(run["peer_id"])
        legacy = await self._context_service.assemble(
            conversation_kind=kind,
            peer_id=peer_id,
            actor_qq=actor_qq,
            owner_qq=owner_qq,
            bot_qq=bot_qq,
        )
        profiles, understandings = await self._repository.active_persona_inputs(peer_id)
        user_detail = (
            await self._repository.user_detail(peer_id)
            if kind is ConversationKind.PRIVATE
            else None
        )
        private_allowed = bool(legacy.isolation["private_user_layers_allowed"])
        sections = [
            ContextSection(
                section_id="core-identity",
                source_class=ContextSourceClass.CORE_IDENTITY,
                scope=ContextScope.GLOBAL,
                content=_json(
                    {
                        "identity": legacy.core_identity,
                        "directives": legacy.core_directives,
                    }
                ),
                policy_decision=ContextPolicyDecision.ALLOWED,
                record_ids=("core-identity",),
                policy_reason="immutable_identity",
            ),
            ContextSection(
                section_id="authorization",
                source_class=ContextSourceClass.AUTHORIZATION,
                scope=ContextScope.CONVERSATION,
                content=_json(
                    {
                        "authenticated_creator": legacy.authenticated_creator,
                        "private_user_layers_allowed": private_allowed,
                        "history_mode": (
                            user_detail["history_policy"]["mode"] if user_detail else "deny"
                        ),
                    }
                ),
                policy_decision=ContextPolicyDecision.ALLOWED,
                subject_qq=peer_id if kind is ConversationKind.PRIVATE else None,
                record_ids=tuple(
                    item["id"] for item in (user_detail or {}).get("identity_evidence", [])
                ),
                policy_reason="platform_identity_and_stored_policy",
            ),
            _conversation_section(run),
            _persona_section(kind, peer_id, profiles, legacy.persona, private_allowed),
            _knowledge_section(kind, peer_id, profiles, understandings, private_allowed),
            _memory_section(kind, peer_id, legacy.memories, private_allowed),
            await self._history_section(kind, peer_id, run, user_detail, private_allowed),
        ]
        manifest = ContextManifest(
            run_id=run_id,
            conversation_key=run["conversation_key"],
            sections=tuple(sections),
        )
        try:
            validate_context_isolation(
                manifest,
                conversation_kind=kind,
                peer_id=peer_id,
            )
        except ContextIsolationError as exc:
            raise ContextAssemblyError("context isolation validation failed") from exc
        try:
            budgeted = apply_context_budget(manifest, budget_chars=budget_chars)
        except ContextBudgetError as exc:
            raise ContextAssemblyError(str(exc)) from exc
        manifest = budgeted.manifest
        revision = (
            max(
                (int(item["revision"]) for item in run["context_manifests"]),
                default=0,
            )
            + 1
        )
        stored = await self._repository.store_reply_context_manifest(
            manifest_id=str(uuid4()),
            manifest=manifest,
            revision=revision,
            rendered_content=budgeted.rendered_content,
            budget_chars=budgeted.budget_chars,
            lease_token=lease_token,
        )
        if not stored:
            raise ContextAssemblyError("context manifest revision conflicted or lease was lost")
        return manifest

    async def _history_section(
        self,
        kind: ConversationKind,
        peer_id: str,
        run: dict[str, Any],
        user_detail: dict[str, Any] | None,
        private_allowed: bool,
    ) -> ContextSection:
        if kind is not ConversationKind.PRIVATE or not private_allowed or user_detail is None:
            return _denied_private_section(
                "history",
                ContextSourceClass.HISTORY,
                peer_id if kind is ConversationKind.PRIVATE else None,
                "group_or_actor_mismatch",
            )
        policy = user_detail["history_policy"]
        mode = str(policy.get("mode", "deny"))
        if mode != "selected_range":
            return _denied_private_section(
                "history", ContextSourceClass.HISTORY, peer_id, f"history_mode_{mode}"
            )
        bounds = _history_bounds(policy.get("selected_from"), policy.get("selected_to"))
        if bounds is None:
            return _denied_private_section(
                "history", ContextSourceClass.HISTORY, peer_id, "invalid_selected_range"
            )
        first_trigger_at = min(
            datetime.fromisoformat(str(item["occurred_at"])).astimezone(UTC)
            for item in run["triggers"]
        )
        history_start_at = datetime.fromisoformat(bounds[0])
        history_end_at = min(datetime.fromisoformat(bounds[1]), first_trigger_at)
        if history_end_at <= history_start_at:
            messages = []
        else:
            limit = max(1, min(int(policy.get("max_messages") or 50), 200))
            messages = await self._repository.chatlog_messages(
                kind=kind.value,
                peer_id=peer_id,
                start_iso=bounds[0],
                end_iso=history_end_at.isoformat(),
                limit=limit,
            )
        trigger_ids = {item["message_id"] for item in run["triggers"]}
        history = [item for item in messages if item["id"] not in trigger_ids]
        return ContextSection(
            section_id="history",
            source_class=ContextSourceClass.HISTORY,
            scope=ContextScope.USER,
            subject_qq=peer_id,
            content=_json(history),
            policy_decision=ContextPolicyDecision.ALLOWED,
            record_ids=tuple(item["id"] for item in history),
            policy_reason="selected_range_policy",
        )


def _conversation_section(run: dict[str, Any]) -> ContextSection:
    content = [
        {
            "message_id": item["message_id"],
            "sequence": item["sequence"],
            "sender_id": item["sender_id"],
            "occurred_at": item["occurred_at"],
            "plain_text": item["plain_text"],
            "segments": item["segments"],
        }
        for item in run["triggers"]
    ]
    return ContextSection(
        section_id="trigger-conversation",
        source_class=ContextSourceClass.CONVERSATION,
        scope=ContextScope.CONVERSATION,
        content=_json(content),
        policy_decision=ContextPolicyDecision.ALLOWED,
        subject_qq=run["subject_user_qq"],
        record_ids=tuple(item["message_id"] for item in run["triggers"]),
        policy_reason="durable_reply_triggers",
    )


def _persona_section(
    kind: ConversationKind,
    peer_id: str,
    profiles: tuple[Any, ...],
    persona: dict[str, Any],
    private_allowed: bool,
) -> ContextSection:
    eligible = tuple(
        profile
        for profile in profiles
        if profile.source is ProfileSource.MANUAL
        and (
            profile.scope is ProfileScope.GLOBAL
            or (
                kind is ConversationKind.PRIVATE and private_allowed and profile.scope_id == peer_id
            )
        )
    )
    return ContextSection(
        section_id="persona",
        source_class=ContextSourceClass.PERSONA,
        scope=ContextScope.CONVERSATION,
        content=_json(
            {
                "base_definition": persona["base_definition"],
                "private_definition": persona["private_definition"],
            }
        ),
        policy_decision=ContextPolicyDecision.ALLOWED,
        subject_qq=peer_id if kind is ConversationKind.PRIVATE else None,
        record_ids=tuple(profile.id for profile in eligible),
        policy_reason="active_manual_profiles",
    )


def _knowledge_section(
    kind: ConversationKind,
    peer_id: str,
    profiles: tuple[Any, ...],
    understandings: tuple[Any, ...],
    private_allowed: bool,
) -> ContextSection:
    if kind is not ConversationKind.PRIVATE or not private_allowed:
        return _denied_private_section(
            "knowledge",
            ContextSourceClass.KNOWLEDGE,
            peer_id if kind is ConversationKind.PRIVATE else None,
            "group_or_actor_mismatch",
        )
    derived = tuple(
        profile
        for profile in profiles
        if profile.source is ProfileSource.DOCUMENT_DERIVED and profile.scope_id == peer_id
    )
    matching_understandings = tuple(item for item in understandings if item.user_qq == peer_id)
    return ContextSection(
        section_id="knowledge",
        source_class=ContextSourceClass.KNOWLEDGE,
        scope=ContextScope.USER,
        subject_qq=peer_id,
        content=_json(
            {
                "derived_personas": [profile.traits for profile in derived],
                "user_understandings": [item.summary for item in matching_understandings],
            }
        ),
        policy_decision=ContextPolicyDecision.ALLOWED,
        record_ids=tuple(
            [profile.id for profile in derived] + [item.id for item in matching_understandings]
        ),
        policy_reason="active_approved_derivations",
    )


def _memory_section(
    kind: ConversationKind,
    peer_id: str,
    memories: tuple[dict[str, Any], ...],
    private_allowed: bool,
) -> ContextSection:
    if kind is not ConversationKind.PRIVATE or not private_allowed:
        return _denied_private_section(
            "memory",
            ContextSourceClass.MEMORY,
            peer_id if kind is ConversationKind.PRIVATE else None,
            "group_or_actor_mismatch",
        )
    return ContextSection(
        section_id="memory",
        source_class=ContextSourceClass.MEMORY,
        scope=ContextScope.USER,
        subject_qq=peer_id,
        content=_json(memories),
        policy_decision=ContextPolicyDecision.ALLOWED,
        record_ids=tuple(str(item["id"]) for item in memories),
        policy_reason="active_unexpired_memories",
    )


def _denied_private_section(
    section_id: str,
    source_class: ContextSourceClass,
    subject_qq: str | None,
    reason: str,
) -> ContextSection:
    return ContextSection(
        section_id=section_id,
        source_class=source_class,
        scope=ContextScope.USER if subject_qq else ContextScope.GROUP,
        subject_qq=subject_qq,
        content="",
        policy_decision=ContextPolicyDecision.DENIED,
        policy_reason=reason,
    )


def _history_bounds(selected_from: Any, selected_to: Any) -> tuple[str, str] | None:
    if not selected_from or not selected_to:
        return None
    try:
        start = _parse_boundary(str(selected_from), end=False)
        end = _parse_boundary(str(selected_to), end=True)
    except ValueError:
        return None
    if start >= end:
        return None
    return start.isoformat(), end.isoformat()


def _parse_boundary(value: str, *, end: bool) -> datetime:
    if len(value) == 10:
        day = date.fromisoformat(value)
        boundary = datetime.combine(day, time.min, tzinfo=UTC)
        return boundary + timedelta(days=1) if end else boundary
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("history boundary must be timezone-aware")
    return parsed.astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
