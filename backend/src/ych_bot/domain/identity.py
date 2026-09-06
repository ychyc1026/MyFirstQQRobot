"""User identity, friend lifecycle, history authorization, and privacy types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FriendState(StrEnum):
    UNKNOWN = "unknown"
    EXISTING_FRIEND = "existing_friend"
    NEW_FRIEND = "new_friend"
    NOT_FRIEND = "not_friend"


class IdentityEvidenceType(StrEnum):
    BASELINE_SNAPSHOT = "baseline_snapshot"
    FRIEND_ADD_EVENT = "friend_add_event"
    MESSAGE_OBSERVED = "message_observed"
    OWNER_OVERRIDE = "owner_override"
    OWNER_OVERRIDE_RELEASED = "owner_override_released"
    IMPORTED_PROFILE = "imported_profile"


class HistoryAccessMode(StrEnum):
    DENY = "deny"
    FILE_IMPORT_ONLY = "file_import_only"
    SELECTED_RANGE = "selected_range"
    ONE_TIME = "one_time"


class QzoneProfileAccessMode(StrEnum):
    DENY = "deny"
    ONE_TIME = "one_time"
    TTL = "ttl"


class PrivacyDataClass(StrEnum):
    PUBLIC_PROFILE = "public_profile"
    MESSAGE_CONTENT = "message_content"
    IMPORTED_DOCUMENT = "imported_document"
    QZONE_CONTENT = "qzone_content"
    DERIVED_MEMORY = "derived_memory"
    PERSONA_PROFILE = "persona_profile"


class PrivacyRequestKind(StrEnum):
    EXPORT = "export"
    DELETE = "delete"
    FREEZE = "freeze"
    UNFREEZE = "unfreeze"


class PrivacyRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RelationshipDecision:
    state: FriendState
    reason: str
    confidence: float


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    mode: HistoryAccessMode = HistoryAccessMode.DENY
    max_messages: int | None = None
    allow_media: bool = False
    one_time_remaining: int = 0

    @property
    def allows_live_history_read(self) -> bool:
        if self.mode is HistoryAccessMode.SELECTED_RANGE:
            return True
        return self.mode is HistoryAccessMode.ONE_TIME and self.one_time_remaining > 0


def decide_friend_state(
    *,
    owner_override: FriendState | None = None,
    present_in_baseline: bool = False,
    friend_add_after_baseline: bool = False,
    friend_add_without_baseline: bool = False,
) -> RelationshipDecision:
    if owner_override is not None:
        return RelationshipDecision(owner_override, "owner_override", 1.0)
    if friend_add_after_baseline:
        return RelationshipDecision(FriendState.NEW_FRIEND, "friend_add_after_baseline", 1.0)
    if present_in_baseline:
        return RelationshipDecision(FriendState.EXISTING_FRIEND, "baseline_snapshot", 1.0)
    if friend_add_without_baseline:
        return RelationshipDecision(
            FriendState.UNKNOWN,
            "friend_add_seen_before_baseline",
            0.5,
        )
    return RelationshipDecision(FriendState.UNKNOWN, "insufficient_evidence", 0.0)
