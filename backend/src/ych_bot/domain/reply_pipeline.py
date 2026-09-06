"""Provider-independent contracts for durable reply orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .models import ConversationKind, MessageSegment, UnifiedMessage


class ReplyRunStage(StrEnum):
    PENDING = "pending"
    SETTLING = "settling"
    ASSEMBLING_CONTEXT = "assembling_context"
    CALLING_MODEL = "calling_model"
    PLANNING_REPLY = "planning_reply"
    CREATING_OUTBOX = "creating_outbox"
    AWAITING_DELIVERY = "awaiting_delivery"
    AWAITING_APPROVAL = "awaiting_approval"
    SHADOW_COMPLETED = "shadow_completed"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_REPLY_STAGES


TERMINAL_REPLY_STAGES = frozenset(
    {
        ReplyRunStage.COMPLETED,
        ReplyRunStage.SHADOW_COMPLETED,
        ReplyRunStage.SUPPRESSED,
        ReplyRunStage.FAILED,
        ReplyRunStage.CANCELLED,
    }
)

REPLY_STAGE_TRANSITIONS: dict[ReplyRunStage, frozenset[ReplyRunStage]] = {
    ReplyRunStage.PENDING: frozenset(
        {ReplyRunStage.SETTLING, ReplyRunStage.SUPPRESSED, ReplyRunStage.CANCELLED}
    ),
    ReplyRunStage.SETTLING: frozenset(
        {
            ReplyRunStage.ASSEMBLING_CONTEXT,
            ReplyRunStage.SUPPRESSED,
            ReplyRunStage.CANCELLED,
        }
    ),
    ReplyRunStage.ASSEMBLING_CONTEXT: frozenset(
        {ReplyRunStage.CALLING_MODEL, ReplyRunStage.SUPPRESSED, ReplyRunStage.FAILED}
    ),
    ReplyRunStage.CALLING_MODEL: frozenset({ReplyRunStage.PLANNING_REPLY, ReplyRunStage.FAILED}),
    ReplyRunStage.PLANNING_REPLY: frozenset(
        {
            ReplyRunStage.CREATING_OUTBOX,
            ReplyRunStage.AWAITING_APPROVAL,
            ReplyRunStage.SHADOW_COMPLETED,
            ReplyRunStage.SUPPRESSED,
            ReplyRunStage.FAILED,
        }
    ),
    ReplyRunStage.AWAITING_APPROVAL: frozenset(
        {ReplyRunStage.CREATING_OUTBOX, ReplyRunStage.SUPPRESSED, ReplyRunStage.CANCELLED}
    ),
    ReplyRunStage.CREATING_OUTBOX: frozenset(
        {ReplyRunStage.AWAITING_DELIVERY, ReplyRunStage.FAILED}
    ),
    ReplyRunStage.AWAITING_DELIVERY: frozenset({ReplyRunStage.COMPLETED, ReplyRunStage.FAILED}),
    **{stage: frozenset() for stage in TERMINAL_REPLY_STAGES},
}


class ReplyActivationMode(StrEnum):
    OBSERVE_ONLY = "observe_only"
    SHADOW = "shadow"
    OWNER_APPROVED = "owner_approved"
    LIMITED_AUTO = "limited_auto"
    AUTO = "auto"

    @property
    def allows_model(self) -> bool:
        return self is not ReplyActivationMode.OBSERVE_ONLY

    @property
    def allows_delivery(self) -> bool:
        return self in {
            ReplyActivationMode.OWNER_APPROVED,
            ReplyActivationMode.LIMITED_AUTO,
            ReplyActivationMode.AUTO,
        }


class ReplyRuntimeLifecycle(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"


class ReplyRuntimeFailureCode(StrEnum):
    MODE_BLOCKED = "mode_blocked"
    EMERGENCY_PAUSED = "emergency_paused"
    MODEL_GATE_BLOCKED = "model_gate_blocked"
    DELIVERY_GATE_BLOCKED = "delivery_gate_blocked"
    CONVERSATION_INELIGIBLE = "conversation_ineligible"
    GROUP_NOT_MENTIONED = "group_not_mentioned"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_STALE = "approval_stale"
    LEASE_LOST = "lease_lost"
    WORKER_FAILED = "worker_failed"


@dataclass(frozen=True, slots=True)
class ReplyRuntimeDecision:
    bot_qq: str
    requested_mode: ReplyActivationMode
    effective_mode: ReplyActivationMode
    revision: int
    emergency_paused: bool
    blockers: tuple[str, ...] = ()

    @property
    def model_allowed(self) -> bool:
        return self.effective_mode.allows_model and not self.emergency_paused

    @property
    def delivery_allowed(self) -> bool:
        return self.effective_mode.allows_delivery and not self.emergency_paused


@dataclass(frozen=True, slots=True)
class ReplyEligibilityScope:
    bot_qq: str
    conversation_key: str
    enabled: bool
    revision: int


@dataclass(frozen=True, slots=True)
class ReplyApprovalBinding:
    run_id: str
    plan_hash: str
    runtime_revision: int
    expires_at: datetime


def can_transition_reply_stage(current: ReplyRunStage, target: ReplyRunStage) -> bool:
    return target in REPLY_STAGE_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    message_id: str
    event_key: str
    bot_qq: str
    conversation_kind: ConversationKind
    peer_id: str
    sender_qq: str
    occurred_at: datetime
    segments: tuple[MessageSegment, ...]
    reply_to_message_id: str | None = None

    @classmethod
    def from_unified(cls, message: UnifiedMessage) -> MessageEnvelope:
        return cls(
            message_id=message.id,
            event_key=message.event_key,
            bot_qq=message.bot_qq,
            conversation_kind=message.conversation_kind,
            peer_id=message.conversation_id,
            sender_qq=message.sender_id,
            occurred_at=message.occurred_at,
            segments=message.segments,
            reply_to_message_id=message.reply_to_message_id,
        )

    @property
    def conversation_key(self) -> str:
        return f"{self.bot_qq}:{self.conversation_kind.value}:{self.peer_id}"


class ContextSourceClass(StrEnum):
    CORE_IDENTITY = "core_identity"
    AUTHORIZATION = "authorization"
    CONVERSATION = "conversation"
    PERSONA = "persona"
    USER_UNDERSTANDING = "user_understanding"
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    HISTORY = "history"


class ContextScope(StrEnum):
    GLOBAL = "global"
    CONVERSATION = "conversation"
    USER = "user"
    GROUP = "group"


class ContextPolicyDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class ContextSection:
    section_id: str
    source_class: ContextSourceClass
    scope: ContextScope
    content: str
    policy_decision: ContextPolicyDecision
    subject_qq: str | None = None
    record_ids: tuple[str, ...] = ()
    policy_reason: str = ""
    truncated: bool = False
    omitted_chars: int = 0

    def __post_init__(self) -> None:
        if self.scope is ContextScope.USER and not self.subject_qq:
            raise ValueError("user-scoped context requires subject_qq")
        if self.policy_decision is ContextPolicyDecision.DENIED and self.content:
            raise ValueError("denied context must not contain renderable content")
        if self.omitted_chars < 0:
            raise ValueError("omitted_chars cannot be negative")


@dataclass(frozen=True, slots=True)
class ContextManifest:
    run_id: str
    conversation_key: str
    sections: tuple[ContextSection, ...]

    @property
    def included(self) -> tuple[ContextSection, ...]:
        return tuple(
            section
            for section in self.sections
            if section.policy_decision is ContextPolicyDecision.ALLOWED
        )

    @property
    def denied(self) -> tuple[ContextSection, ...]:
        return tuple(
            section
            for section in self.sections
            if section.policy_decision is ContextPolicyDecision.DENIED
        )


@dataclass(frozen=True, slots=True)
class ReplyBubble:
    sequence: int
    idempotency_key: str
    segments: tuple[MessageSegment, ...]

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("reply bubble sequence starts at 1")
        if not self.idempotency_key.strip():
            raise ValueError("reply bubble requires idempotency_key")
        if not self.segments:
            raise ValueError("reply bubble requires at least one segment")


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    run_id: str
    bubbles: tuple[ReplyBubble, ...]

    def __post_init__(self) -> None:
        if not self.bubbles:
            raise ValueError("reply plan requires at least one bubble")
        sequences = tuple(bubble.sequence for bubble in self.bubbles)
        if sequences != tuple(range(1, len(self.bubbles) + 1)):
            raise ValueError("reply bubble sequences must be contiguous and ordered")
        keys = {bubble.idempotency_key for bubble in self.bubbles}
        if len(keys) != len(self.bubbles):
            raise ValueError("reply bubble idempotency keys must be unique")


class ReplyFailureCategory(StrEnum):
    UNSUPPORTED_INPUT = "unsupported_input"
    POLICY_DENIED = "policy_denied"
    CONTEXT = "context"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_RESPONSE = "model_response"
    OUTBOX = "outbox"
    DELIVERY_REJECTED = "delivery_rejected"
    DELIVERY_UNKNOWN = "delivery_unknown"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ReplyFailure:
    code: str
    category: ReplyFailureCategory
    retryable: bool
    safe_detail: str

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("reply failure requires a code")
        if not self.safe_detail.strip():
            raise ValueError("reply failure requires a sanitized detail")
