"""Profile-aware readiness probes and process-scoped evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from inspect import isawaitable
from typing import Any, Protocol
from uuid import uuid4

from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    LauncherPreflightResult,
    ProbeStatus,
    ReadinessBlocker,
    ReadinessDecision,
    ReadinessDecisionStatus,
    ReadinessProbeResult,
    ReadinessProfile,
    ReadinessReasonCode,
    require_aware_utc,
)

ProbeValue = dict[str, Any]
ProbeCallback = Callable[[], ProbeValue | Awaitable[ProbeValue]]
ScopeProbeCallback = Callable[[CapabilityScope], ProbeValue | Awaitable[ProbeValue]]


@dataclass(frozen=True, slots=True)
class ProbeContext:
    bot_qq: str
    process_instance_id: str
    profile: ReadinessProfile
    capability_scope: CapabilityScope
    now: datetime


class ReadinessProbe(Protocol):
    code: str

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult: ...


def _result(
    context: ProbeContext,
    *,
    code: str,
    status: ProbeStatus,
    source: str,
    source_revision: str,
    safe_detail: str,
    remediation_code: str = "",
    evidence: Mapping[str, object] | None = None,
    ttl: timedelta = timedelta(seconds=30),
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
    scope: CapabilityScope | None = None,
) -> ReadinessProbeResult:
    observed = require_aware_utc(observed_at or context.now, "observed_at")
    expiry = require_aware_utc(expires_at or observed + ttl, "expires_at")
    return ReadinessProbeResult(
        probe_code=code,
        status=status,
        capability_scope=scope or context.capability_scope,
        freshness=EvidenceFreshness(observed_at=observed, expires_at=expiry),
        source=source,
        source_revision=source_revision,
        safe_detail=safe_detail,
        remediation_code=remediation_code,
        evidence=tuple(
            sorted(
                (str(key), str(value).lower() if isinstance(value, bool) else str(value))
                for key, value in (evidence or {}).items()
            )
        ),
    )


async def _call(callback: ProbeCallback) -> ProbeValue:
    value = callback()
    return await value if isawaitable(value) else value


class SharedLauncherEvidence:
    def __init__(
        self,
        *,
        result: LauncherPreflightResult | None = None,
        refresher: Callable[[], Awaitable[LauncherPreflightResult]] | None = None,
    ) -> None:
        self.result = result
        self.refresher = refresher
        self._lock: asyncio.Lock | None = None

    def _lock_for_loop(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def resolve(self, now: datetime) -> LauncherPreflightResult | None:
        async with self._lock_for_loop():
            result = self.result
            if result is None:
                return None
            if now <= result.expires_at:
                return result
            if self.refresher is None:
                return result
            refreshed = await self.refresher()
            self.result = refreshed
            return refreshed


class LauncherEvidenceProbe:
    def __init__(
        self,
        code: str,
        *,
        launcher_result: LauncherPreflightResult | None = None,
        evidence: SharedLauncherEvidence | None = None,
        expected_process_instance_id: str,
        expected_configuration_fingerprint: str,
    ) -> None:
        if evidence is not None and launcher_result is not None:
            raise ValueError("launcher evidence must be passed once")
        self.code = code
        self._evidence = evidence or SharedLauncherEvidence(result=launcher_result)
        self._process_instance_id = expected_process_instance_id
        self._fingerprint = expected_configuration_fingerprint

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        launcher = await self._evidence.resolve(context.now)
        if launcher is None:
            return _result(
                context,
                code=self.code,
                status=ProbeStatus.UNKNOWN,
                source="launcher",
                source_revision="absent",
                safe_detail="matching launcher evidence is absent",
                remediation_code="restart_through_launcher",
            )
        if launcher.process_instance_id != self._process_instance_id:
            return _result(
                context,
                code=self.code,
                status=ProbeStatus.BLOCKED,
                source="launcher",
                source_revision="process-mismatch",
                safe_detail="launcher evidence belongs to a different process instance",
                remediation_code="restart_through_launcher",
            )
        if launcher.configuration_fingerprint != self._fingerprint:
            return _result(
                context,
                code=self.code,
                status=ProbeStatus.BLOCKED,
                source="launcher",
                source_revision="configuration-mismatch",
                safe_detail="configuration changed after launcher preflight",
                remediation_code="restart_after_configuration_change",
            )
        matching = next((item for item in launcher.probes if item.probe_code == self.code), None)
        if matching is None:
            return _result(
                context,
                code=self.code,
                status=ProbeStatus.UNKNOWN,
                source="launcher",
                source_revision="probe-absent",
                safe_detail="required launcher probe evidence is absent",
                remediation_code="restart_through_launcher",
            )
        if context.now > launcher.expires_at or context.now > matching.freshness.expires_at:
            return replace(matching, status=ProbeStatus.STALE)
        return matching


class RuntimeDatabaseProbe:
    code = "runtime.database"

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        healthy = bool(await self._repository.healthcheck())
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if healthy else ProbeStatus.BLOCKED,
            source="sqlite",
            source_revision="healthcheck-v1",
            safe_detail="database is healthy" if healthy else "database health check failed",
            remediation_code="repair_runtime_database" if not healthy else "",
            scope=CapabilityScope.LOCAL_RUNTIME,
        )


class WorkerCeilingProbe:
    code = "runtime.worker_ceilings"

    def __init__(self, *, configured_modes: Mapping[str, object]) -> None:
        self._configured_modes = dict(configured_modes)

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        invalid = [key for key, value in self._configured_modes.items() if value is None]
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.BLOCKED if invalid else ProbeStatus.PASS,
            source="configuration",
            source_revision=_mapping_revision(self._configured_modes),
            safe_detail=(
                "worker ceilings are explicit"
                if not invalid
                else "one or more worker ceilings are undefined"
            ),
            remediation_code="configure_worker_ceilings" if invalid else "",
            evidence={"configured_worker_count": len(self._configured_modes)},
            ttl=timedelta(minutes=5),
            scope=CapabilityScope.LOCAL_RUNTIME,
        )


class OfflineIsolationProbe:
    code = "offline.isolation"

    def __init__(self, *, state: ProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        state = await _call(self._state)
        isolated = (
            bool(state.get("network_disabled"))
            and bool(state.get("outbox_non_delivering"))
            and bool(state.get("context_isolation"))
        )
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if isolated else ProbeStatus.BLOCKED,
            source="offline-runtime",
            source_revision=_mapping_revision(state),
            safe_detail=(
                "offline adapters, outbox and context isolation are active"
                if isolated
                else "offline runtime isolation is incomplete"
            ),
            remediation_code="disable_real_adapters_for_shadow" if not isolated else "",
            evidence={
                "network_disabled": bool(state.get("network_disabled")),
                "outbox_non_delivering": bool(state.get("outbox_non_delivering")),
                "context_isolation": bool(state.get("context_isolation")),
            },
            scope=CapabilityScope.OFFLINE_INFERENCE,
        )


class DurablePauseProbe:
    code = "runtime.durable_pause"

    def __init__(self, *, state: ScopeProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        value = self._state(context.capability_scope)
        state = await value if isawaitable(value) else value
        paused = bool(state.get("paused"))
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.BLOCKED if paused else ProbeStatus.PASS,
            source="durable-worker-state",
            source_revision=str(state.get("revision", "unknown")),
            safe_detail="persistent pause is active" if paused else "persistent pause is clear",
            remediation_code="review_and_resume_worker" if paused else "",
            evidence={"paused": paused},
        )


class OneBotIdentityProbe:
    code = "onebot.identity"

    def __init__(self, *, state: ProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        state = await _call(self._state)
        connected = bool(state.get("connected"))
        token_configured = bool(state.get("token_configured"))
        authenticated_bot_qq = str(state.get("authenticated_bot_qq") or "")
        exact_bot = authenticated_bot_qq == context.bot_qq
        passed = connected and token_configured and exact_bot
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if passed else ProbeStatus.BLOCKED,
            source="onebot-connection",
            source_revision=str(state.get("connection_revision", "disconnected")),
            safe_detail=(
                "OneBot connection matches the evaluated bot"
                if passed
                else "OneBot authentication, connection or exact bot identity does not match"
            ),
            remediation_code="connect_expected_bot" if not passed else "",
            evidence={
                "connected": connected,
                "token_configured": token_configured,
                "exact_bot": exact_bot,
            },
            ttl=timedelta(seconds=15),
        )


class CapabilityGateProbe:
    code = "capability.gate"

    def __init__(self, *, state: ScopeProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        value = self._state(context.capability_scope)
        state = await value if isawaitable(value) else value
        configured = bool(state.get("configured"))
        active = bool(state.get("active"))
        circuit_available = bool(state.get("circuit_available", True))
        passed = configured and active and circuit_available
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if passed else ProbeStatus.BLOCKED,
            source="capability-gates",
            source_revision=str(state.get("revision", _mapping_revision(state))),
            safe_detail=(
                "capability is configured and active"
                if passed
                else "capability configuration, active gate or circuit is unavailable"
            ),
            remediation_code="open_required_capability_gate" if not passed else "",
            evidence={
                "configured": configured,
                "active": active,
                "circuit_available": circuit_available,
            },
        )


class WorkerLifecycleProbe:
    code = "runtime.worker_lifecycle"

    def __init__(self, *, state: ScopeProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        value = self._state(context.capability_scope)
        state = await value if isawaitable(value) else value
        active = bool(state.get("active"))
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if active else ProbeStatus.BLOCKED,
            source="worker-lifecycle",
            source_revision=str(state.get("revision", "unknown")),
            safe_detail="required worker is active" if active else "required worker is not active",
            remediation_code="start_required_worker" if not active else "",
            evidence={"configured": bool(state.get("configured")), "active": active},
            ttl=timedelta(seconds=15),
        )


class ActivationScopeProbe:
    code = "runtime.activation_scope"

    def __init__(self, *, state: ScopeProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        value = self._state(context.capability_scope)
        state = await value if isawaitable(value) else value
        allowed = bool(state.get("allowed")) and not bool(state.get("emergency_paused"))
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if allowed else ProbeStatus.BLOCKED,
            source="activation-policy",
            source_revision=str(state.get("revision", "unknown")),
            safe_detail=(
                "activation scope permits the capability"
                if allowed
                else "activation scope or emergency pause blocks the capability"
            ),
            remediation_code="review_activation_scope" if not allowed else "",
            evidence={
                "allowed": bool(state.get("allowed")),
                "emergency_paused": bool(state.get("emergency_paused")),
            },
        )


class OwnerAuthorizationProbe:
    code = "runtime.owner_authorization"

    def __init__(self, *, state: ProbeCallback) -> None:
        self._state = state

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        state = await _call(self._state)
        authorized = bool(state.get("authorized"))
        return _result(
            context,
            code=self.code,
            status=ProbeStatus.PASS if authorized else ProbeStatus.BLOCKED,
            source="owner-policy",
            source_revision=str(state.get("revision", "unknown")),
            safe_detail=(
                "current owner authorization is valid"
                if authorized
                else "current owner authorization is absent or mismatched"
            ),
            remediation_code="restore_owner_authorization" if not authorized else "",
            evidence={"authorized": authorized},
            ttl=timedelta(seconds=30),
        )


LOCAL_PROBES = (
    "launcher.configuration",
    "launcher.database",
    "launcher.managed_paths",
    "launcher.admin_auth",
    "launcher.port",
    "runtime.database",
    "runtime.worker_ceilings",
)
OFFLINE_PROBES = (*LOCAL_PROBES, "offline.isolation")
CONTROLLED_BASE_PROBES = (
    *LOCAL_PROBES,
    "runtime.durable_pause",
    "runtime.worker_lifecycle",
    "capability.gate",
    "runtime.activation_scope",
    "runtime.owner_authorization",
)
ONEBOT_SCOPES = frozenset(
    {
        CapabilityScope.QQ_REPLY,
        CapabilityScope.QQ_PROACTIVE,
        CapabilityScope.OWNER_REPORT,
        CapabilityScope.QZONE_PUBLISH,
        CapabilityScope.QZONE_PROFILE,
        CapabilityScope.LIVE_HISTORY,
    }
)


class ReadinessService:
    def __init__(
        self,
        repository: Any,
        *,
        bot_qq: str,
        process_instance_id: str,
        probes: Mapping[str, ReadinessProbe],
        clock: Callable[[], datetime] | None = None,
        per_probe_timeout_seconds: float = 2,
        max_concurrency: int = 4,
    ) -> None:
        if not bot_qq.isdigit() or not process_instance_id:
            raise ValueError("bot QQ and process instance ID are required")
        if per_probe_timeout_seconds <= 0 or max_concurrency < 1:
            raise ValueError("probe timeout and concurrency must be positive")
        self._repository = repository
        self.bot_qq = bot_qq
        self.process_instance_id = process_instance_id
        self._probes = dict(probes)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = per_probe_timeout_seconds
        self._max_concurrency = max_concurrency

    async def evaluate(
        self,
        profile: ReadinessProfile,
        *,
        capability_scope: CapabilityScope,
        persist: bool = True,
    ) -> ReadinessDecision:
        now = require_aware_utc(self._clock(), "clock")
        context = ProbeContext(
            bot_qq=self.bot_qq,
            process_instance_id=self.process_instance_id,
            profile=profile,
            capability_scope=capability_scope,
            now=now,
        )
        required = self.required_probe_codes(profile, capability_scope)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def collect(code: str) -> ReadinessProbeResult:
            provider = self._probes.get(code)
            if provider is None:
                return self._unknown(context, code, "probe provider is not configured")
            try:
                async with semaphore:
                    result = await asyncio.wait_for(
                        provider.collect(context), timeout=self._timeout
                    )
            except TimeoutError:
                return self._unknown(context, code, "probe timed out")
            except Exception:
                return self._unknown(context, code, "probe failed safely")
            if now > result.freshness.expires_at:
                return replace(result, status=ProbeStatus.STALE)
            return result

        results = tuple(await asyncio.gather(*(collect(code) for code in required)))
        blockers = tuple(
            self._blocker(result) for result in results if result.status.blocks_required_probe
        )
        warnings = tuple(
            ReadinessBlocker(
                code=ReadinessReasonCode.REQUIRED_PROBE_BLOCKED,
                probe_code=result.probe_code,
                capability_scope=result.capability_scope,
                safe_detail=result.safe_detail,
            )
            for result in results
            if result.status is ProbeStatus.WARNING
        )
        revision = await self._next_revision(profile, capability_scope)
        decision = ReadinessDecision(
            decision_id=str(uuid4()),
            bot_qq=self.bot_qq,
            process_instance_id=self.process_instance_id,
            profile=profile,
            capability_scope=capability_scope,
            scope_hash=self._scope_hash(profile, capability_scope),
            revision=revision,
            status=(
                ReadinessDecisionStatus.BLOCKED if blockers else ReadinessDecisionStatus.PASSED
            ),
            evaluated_at=now,
            blockers=blockers,
            warnings=warnings,
            probes=results,
            correlation_id=str(uuid4()),
        )
        if persist:
            decision = await self._persist_with_retry(decision)
        return decision

    @staticmethod
    def required_probe_codes(
        profile: ReadinessProfile, capability_scope: CapabilityScope
    ) -> tuple[str, ...]:
        if profile is ReadinessProfile.LOCAL_START:
            return LOCAL_PROBES
        if profile is ReadinessProfile.OFFLINE_SHADOW:
            return OFFLINE_PROBES
        codes = CONTROLLED_BASE_PROBES
        return (*codes, "onebot.identity") if capability_scope in ONEBOT_SCOPES else codes

    async def _next_revision(
        self, profile: ReadinessProfile, capability_scope: CapabilityScope
    ) -> int:
        history = await self._repository.list_readiness_decisions(
            bot_qq=self.bot_qq,
            process_instance_id=self.process_instance_id,
            profile=profile,
            capability_scope=capability_scope,
            limit=500,
        )
        return max((item.revision for item in history), default=0) + 1

    async def _persist_with_retry(self, decision: ReadinessDecision) -> ReadinessDecision:
        current = decision
        for attempt in range(3):
            try:
                await self._repository.create_readiness_decision(current)
                return current
            except sqlite3.IntegrityError:
                if attempt == 2:
                    raise
                revision = await self._next_revision(current.profile, current.capability_scope)
                current = replace(
                    current,
                    decision_id=str(uuid4()),
                    revision=revision,
                )
        raise RuntimeError("readiness decision persistence retry exhausted")

    def _scope_hash(self, profile: ReadinessProfile, capability_scope: CapabilityScope) -> str:
        payload = ":".join(
            (
                self.bot_qq,
                self.process_instance_id,
                profile.value,
                capability_scope.value,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _unknown(context: ProbeContext, code: str, safe_detail: str) -> ReadinessProbeResult:
        return _result(
            context,
            code=code,
            status=ProbeStatus.UNKNOWN,
            source="readiness-service",
            source_revision="unknown",
            safe_detail=safe_detail,
            remediation_code="refresh_probe",
            ttl=timedelta(seconds=1),
        )

    @staticmethod
    def _blocker(result: ReadinessProbeResult) -> ReadinessBlocker:
        if result.status is ProbeStatus.STALE:
            code = ReadinessReasonCode.EVIDENCE_STALE
        elif result.status is ProbeStatus.UNKNOWN:
            code = ReadinessReasonCode.REQUIRED_PROBE_UNKNOWN
        elif result.source_revision == "process-mismatch":
            code = ReadinessReasonCode.PROCESS_INSTANCE_MISMATCH
        elif result.probe_code == "launcher.configuration":
            code = ReadinessReasonCode.CONFIGURATION_INVALID
        elif result.probe_code in {"launcher.database", "runtime.database"}:
            code = ReadinessReasonCode.DATABASE_UNAVAILABLE
        elif result.probe_code == "launcher.managed_paths":
            code = ReadinessReasonCode.PATH_NOT_CONFINED
        elif result.probe_code == "launcher.port":
            code = ReadinessReasonCode.PORT_UNAVAILABLE
        elif result.probe_code == "launcher.admin_auth":
            code = ReadinessReasonCode.ADMIN_AUTH_INVALID
        elif result.probe_code == "runtime.worker_ceilings":
            code = ReadinessReasonCode.WORKER_CEILING_BLOCKED
        elif result.probe_code == "onebot.identity":
            code = ReadinessReasonCode.BOT_IDENTITY_MISMATCH
        elif result.probe_code == "runtime.durable_pause":
            code = ReadinessReasonCode.DURABLE_PAUSE_ACTIVE
        elif result.probe_code == "runtime.owner_authorization":
            code = ReadinessReasonCode.OWNER_AUTHORIZATION_REQUIRED
        elif result.probe_code == "runtime.activation_scope":
            code = ReadinessReasonCode.ACTIVATION_SCOPE_BLOCKED
        elif result.probe_code == "capability.gate":
            code = ReadinessReasonCode.NETWORK_GATE_CLOSED
        else:
            code = ReadinessReasonCode.REQUIRED_PROBE_BLOCKED
        return ReadinessBlocker(
            code=code,
            probe_code=result.probe_code,
            capability_scope=result.capability_scope,
            safe_detail=result.safe_detail,
        )


def _mapping_revision(value: Mapping[str, object]) -> str:
    encoded = repr(sorted((str(key), repr(item)) for key, item in value.items())).encode()
    return hashlib.sha256(encoded).hexdigest()
