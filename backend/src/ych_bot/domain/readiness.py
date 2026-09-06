"""Typed contracts for process-scoped operational readiness evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ReadinessProfile(StrEnum):
    LOCAL_START = "local_start"
    OFFLINE_SHADOW = "offline_shadow"
    CONTROLLED_REAL_EFFECT = "controlled_real_effect"


class ReadinessDecisionStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class ProbeStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    STALE = "stale"

    @property
    def blocks_required_probe(self) -> bool:
        return self in {ProbeStatus.BLOCKED, ProbeStatus.UNKNOWN, ProbeStatus.STALE}


class CapabilityScope(StrEnum):
    LOCAL_RUNTIME = "local_runtime"
    OFFLINE_INFERENCE = "offline_inference"
    CHAT_MODEL = "chat_model"
    VISION_MODEL = "vision_model"
    IMAGE_MODEL = "image_model"
    STATS_MODEL = "stats_model"
    QQ_REPLY = "qq_reply"
    QQ_PROACTIVE = "qq_proactive"
    OWNER_REPORT = "owner_report"
    QZONE_PUBLISH = "qzone_publish"
    QZONE_PROFILE = "qzone_profile"
    LIVE_HISTORY = "live_history"


class ReadinessReasonCode(StrEnum):
    REQUIRED_PROBE_BLOCKED = "required_probe_blocked"
    REQUIRED_PROBE_UNKNOWN = "required_probe_unknown"
    EVIDENCE_STALE = "evidence_stale"
    PROCESS_INSTANCE_MISMATCH = "process_instance_mismatch"
    BOT_IDENTITY_MISMATCH = "bot_identity_mismatch"
    CONFIGURATION_INVALID = "configuration_invalid"
    DATABASE_UNAVAILABLE = "database_unavailable"
    BACKUP_UNVERIFIED = "backup_unverified"
    PATH_NOT_CONFINED = "path_not_confined"
    PORT_UNAVAILABLE = "port_unavailable"
    ADMIN_AUTH_INVALID = "admin_auth_invalid"
    WORKER_CEILING_BLOCKED = "worker_ceiling_blocked"
    DURABLE_PAUSE_ACTIVE = "durable_pause_active"
    NETWORK_GATE_CLOSED = "network_gate_closed"
    ACTIVATION_SCOPE_BLOCKED = "activation_scope_blocked"
    OWNER_AUTHORIZATION_REQUIRED = "owner_authorization_required"


def require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EvidenceFreshness:
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        observed = require_aware_utc(self.observed_at, "observed_at")
        expires = require_aware_utc(self.expires_at, "expires_at")
        if expires < observed:
            raise ValueError("expires_at must not precede observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)

    def status_at(self, now: datetime) -> ProbeStatus:
        return (
            ProbeStatus.STALE
            if require_aware_utc(now, "now") > self.expires_at
            else ProbeStatus.PASS
        )


@dataclass(frozen=True, slots=True)
class ReadinessProbeResult:
    probe_code: str
    status: ProbeStatus
    capability_scope: CapabilityScope
    freshness: EvidenceFreshness
    source: str
    source_revision: str
    safe_detail: str = ""
    remediation_code: str = ""
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.probe_code or not self.source or not self.source_revision:
            raise ValueError("probe code, source and source revision are required")
        if any(not key for key, _value in self.evidence):
            raise ValueError("evidence keys must not be empty")


@dataclass(frozen=True, slots=True)
class ReadinessBlocker:
    code: ReadinessReasonCode
    probe_code: str
    capability_scope: CapabilityScope
    safe_detail: str = ""


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    decision_id: str
    bot_qq: str
    process_instance_id: str
    profile: ReadinessProfile
    capability_scope: CapabilityScope
    scope_hash: str
    revision: int
    status: ReadinessDecisionStatus
    evaluated_at: datetime
    blockers: tuple[ReadinessBlocker, ...] = ()
    warnings: tuple[ReadinessBlocker, ...] = ()
    probes: tuple[ReadinessProbeResult, ...] = ()
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id or not self.process_instance_id or not self.scope_hash:
            raise ValueError("decision, process instance and scope hash are required")
        if not self.bot_qq.isdigit():
            raise ValueError("bot_qq must contain digits only")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        object.__setattr__(
            self, "evaluated_at", require_aware_utc(self.evaluated_at, "evaluated_at")
        )
        if self.status is ReadinessDecisionStatus.PASSED and self.blockers:
            raise ValueError("a passing readiness decision cannot contain blockers")


@dataclass(frozen=True, slots=True)
class LauncherPreflightResult:
    process_instance_id: str
    configuration_fingerprint: str
    observed_at: datetime
    expires_at: datetime
    probes: tuple[ReadinessProbeResult, ...]

    def __post_init__(self) -> None:
        if not self.process_instance_id or not self.configuration_fingerprint:
            raise ValueError("launcher process instance and configuration fingerprint are required")
        observed = require_aware_utc(self.observed_at, "observed_at")
        expires = require_aware_utc(self.expires_at, "expires_at")
        if expires < observed:
            raise ValueError("launcher evidence expiry must not precede observation")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)

    @property
    def passed(self) -> bool:
        return bool(self.probes) and not any(
            probe.status.blocks_required_probe for probe in self.probes
        )
