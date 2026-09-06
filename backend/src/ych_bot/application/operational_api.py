"""Authenticated, sanitized operational-readiness and retention use cases."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
    QuarantineBatchState,
)
from ych_bot.domain.readiness import (
    CapabilityScope,
    ProbeStatus,
    ReadinessDecision,
    ReadinessDecisionStatus,
    ReadinessProfile,
)

from .auth import AdminSessionService
from .readiness import ReadinessService
from .retention import RetentionError, RetentionService


class OperationalApiError(RuntimeError):
    """Stable, localizable application-boundary failure."""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OperationalActor:
    actor_id: str
    actor_source: str
    bot_qq: str
    process_instance_id: str


class OperationalApiService:
    """One policy authority shared by dashboard APIs and owner-control callers."""

    def __init__(
        self,
        repository: Any,
        admin_sessions: AdminSessionService,
        readiness: ReadinessService,
        retention: RetentionService,
        *,
        bot_qq: str,
        process_instance_id: str,
        configured_owner_qq: str,
        clock: Any | None = None,
    ) -> None:
        if not bot_qq.isdigit() or not configured_owner_qq.isdigit() or not process_instance_id:
            raise ValueError("operational API requires exact bot, owner, and process identity")
        self._repository = repository
        self._admin_sessions = admin_sessions
        self._readiness = readiness
        self._retention = retention
        self.bot_qq = bot_qq
        self.process_instance_id = process_instance_id
        self._configured_owner_qq = configured_owner_qq
        self._clock = clock or (lambda: datetime.now(UTC))

    async def admin_context(
        self,
        authorization: str,
        *,
        bot_qq: str | None = None,
        process_instance_id: str | None = None,
        require_current_instance: bool = False,
    ) -> OperationalActor:
        if not self._admin_sessions.configured:
            raise OperationalApiError(
                "admin_auth_not_configured",
                "dashboard administrator authentication is not configured",
                status_code=503,
            )
        token = self._bearer_token(authorization)
        if not await self._admin_sessions.authenticate(token):
            raise OperationalApiError(
                "admin_session_invalid",
                "dashboard administrator session is invalid or expired",
                status_code=401,
            )
        self._validate_scope(
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=require_current_instance,
        )
        owner_qq = await self._current_owner()
        return OperationalActor(
            actor_id=owner_qq,
            actor_source="dashboard",
            bot_qq=self.bot_qq,
            process_instance_id=self.process_instance_id,
        )

    async def owner_context(
        self,
        actor_qq: str,
        *,
        bot_qq: str,
        process_instance_id: str,
    ) -> OperationalActor:
        self._validate_scope(
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=True,
        )
        owner_qq = await self._current_owner()
        if actor_qq != owner_qq:
            raise OperationalApiError(
                "owner_authorization_required",
                "only the current owner may perform this operation",
                status_code=403,
            )
        return OperationalActor(
            actor_id=owner_qq,
            actor_source="owner_qq",
            bot_qq=self.bot_qq,
            process_instance_id=self.process_instance_id,
        )

    async def readiness_summary(
        self,
        authorization: str,
        *,
        controlled_scope: CapabilityScope = CapabilityScope.QQ_REPLY,
        bot_qq: str | None = None,
    ) -> dict[str, Any]:
        await self.admin_context(authorization, bot_qq=bot_qq)
        self._validate_profile_scope(ReadinessProfile.CONTROLLED_REAL_EFFECT, controlled_scope)
        requested = (
            (ReadinessProfile.LOCAL_START, CapabilityScope.LOCAL_RUNTIME),
            (ReadinessProfile.OFFLINE_SHADOW, CapabilityScope.OFFLINE_INFERENCE),
            (ReadinessProfile.CONTROLLED_REAL_EFFECT, controlled_scope),
        )
        items = []
        for profile, scope in requested:
            decision = await self._latest_decision(
                profile=profile,
                capability_scope=scope,
                current_instance_only=True,
            )
            items.append(self._summary_item(profile, scope, decision))
        return {
            "bot_qq": self.bot_qq,
            "process_instance_id": self.process_instance_id,
            "controlled_scope": controlled_scope.value,
            "profiles": items,
        }

    async def readiness_detail(
        self,
        authorization: str,
        *,
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        bot_qq: str | None = None,
        process_instance_id: str | None = None,
    ) -> dict[str, Any]:
        await self.admin_context(
            authorization,
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
        )
        self._validate_profile_scope(profile, capability_scope)
        decision = await self._latest_decision(
            profile=profile,
            capability_scope=capability_scope,
            current_instance_only=True,
        )
        if decision is None:
            return {
                "bot_qq": self.bot_qq,
                "process_instance_id": self.process_instance_id,
                "profile": profile.value,
                "capability_scope": capability_scope.value,
                "evaluated": False,
                "status": "not_evaluated",
            }
        return self._decision_dto(decision, current_instance=True, include_probes=True)

    async def refresh_readiness(
        self,
        authorization: str,
        *,
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        bot_qq: str,
        process_instance_id: str,
    ) -> dict[str, Any]:
        actor = await self.admin_context(
            authorization,
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=True,
        )
        self._validate_profile_scope(profile, capability_scope)
        decision = await self._readiness.evaluate(
            profile,
            capability_scope=capability_scope,
        )
        await self._repository.record_system_audit(
            "readiness.api_refreshed",
            decision.decision_id,
            {
                "actor_id": actor.actor_id,
                "actor_source": actor.actor_source,
                "bot_qq": self.bot_qq,
                "process_instance_id": self.process_instance_id,
                "profile": profile.value,
                "capability_scope": capability_scope.value,
                "decision_revision": decision.revision,
                "correlation_id": decision.correlation_id,
            },
        )
        return self._decision_dto(decision, current_instance=True, include_probes=True)

    async def readiness_history(
        self,
        authorization: str,
        *,
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        include_historical_instances: bool,
        limit: int,
        bot_qq: str | None = None,
    ) -> dict[str, Any]:
        await self.admin_context(authorization, bot_qq=bot_qq)
        self._validate_profile_scope(profile, capability_scope)
        if not 1 <= limit <= 200:
            raise OperationalApiError(
                "invalid_limit",
                "readiness history limit must be between 1 and 200",
                status_code=422,
            )
        decisions = await self._repository.list_readiness_decisions(
            bot_qq=self.bot_qq,
            process_instance_id=(
                None if include_historical_instances else self.process_instance_id
            ),
            profile=profile,
            capability_scope=capability_scope,
            limit=limit,
        )
        return {
            "bot_qq": self.bot_qq,
            "profile": profile.value,
            "capability_scope": capability_scope.value,
            "items": [
                self._decision_dto(
                    item,
                    current_instance=item.process_instance_id == self.process_instance_id,
                    include_probes=False,
                )
                for item in decisions
            ],
        }

    async def list_artifacts(
        self,
        authorization: str,
        *,
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        artifact_type: ManagedArtifactType | None = None,
        verification_state: ArtifactVerificationState | None = None,
        reference_state: ArtifactReferenceState | None = None,
        retention_state: ArtifactRetentionState | None = None,
        limit: int = 500,
        bot_qq: str | None = None,
    ) -> dict[str, Any]:
        await self.admin_context(authorization, bot_qq=bot_qq)
        self._validate_owner_filter(owner_scope, owner_qq)
        if not 1 <= limit <= 1000:
            raise OperationalApiError(
                "invalid_limit", "artifact limit must be between 1 and 1000", status_code=422
            )
        artifacts = self._filter_artifacts(
            await self._repository.all_managed_artifacts(limit=10_000),
            owner_scope=owner_scope,
            owner_qq=owner_qq,
            artifact_type=artifact_type,
            verification_state=verification_state,
            reference_state=reference_state,
            retention_state=retention_state,
        )[:limit]
        return {
            "items": [self._artifact_dto(item) for item in artifacts],
            "count": len(artifacts),
            "paths_exposed": False,
        }

    async def refresh_artifacts(
        self,
        authorization: str,
        *,
        bot_qq: str,
        process_instance_id: str,
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
    ) -> dict[str, Any]:
        actor = await self.admin_context(
            authorization,
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=True,
        )
        self._validate_owner_filter(owner_scope, owner_qq)
        correlation_id = str(uuid4())
        artifacts = self._filter_artifacts(
            await self._retention.refresh_classification(),
            owner_scope=owner_scope,
            owner_qq=owner_qq,
        )
        await self._repository.record_system_audit(
            "artifacts.api_refreshed",
            correlation_id,
            {
                "actor_id": actor.actor_id,
                "actor_source": actor.actor_source,
                "bot_qq": self.bot_qq,
                "process_instance_id": self.process_instance_id,
                "owner_scope": owner_scope.value if owner_scope else "all",
                "owner_qq": owner_qq,
                "artifact_ids": [item.artifact_id for item in artifacts],
                "count": len(artifacts),
                "bytes": sum(item.size_bytes for item in artifacts),
                "correlation_id": correlation_id,
                "filesystem_mutated": False,
            },
        )
        return {
            "items": [self._artifact_dto(item) for item in artifacts],
            "count": len(artifacts),
            "correlation_id": correlation_id,
            "filesystem_mutated": False,
        }

    async def create_retention_preview(
        self,
        authorization: str,
        *,
        bot_qq: str,
        process_instance_id: str,
        target_batch_type: str,
        owner_scope: ArtifactOwnerScope,
        owner_qq: str | None,
    ) -> dict[str, Any]:
        actor = await self.admin_context(
            authorization,
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=True,
        )
        self._validate_owner_filter(owner_scope, owner_qq)
        return await self._retention.create_preview(
            actor_id=actor.actor_id,
            actor_source=actor.actor_source,
            target_batch_type=target_batch_type,
            owner_scope=owner_scope,
            owner_qq=owner_qq,
        )

    async def confirm_retention(
        self,
        authorization: str,
        *,
        bot_qq: str,
        process_instance_id: str,
        confirmation_token: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        actor = await self.admin_context(
            authorization,
            bot_qq=bot_qq,
            process_instance_id=process_instance_id,
            require_current_instance=True,
        )
        return await self._retention.confirm(
            confirmation_token=confirmation_token,
            actor_id=actor.actor_id,
            expected_revision=expected_revision,
        )

    async def quarantine_history(
        self,
        authorization: str,
        *,
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        state: QuarantineBatchState | None = None,
        bot_qq: str | None = None,
    ) -> dict[str, Any]:
        await self.admin_context(authorization, bot_qq=bot_qq)
        self._validate_owner_filter(owner_scope, owner_qq)
        batches = await self._repository.list_quarantine_batches(states=(state,) if state else None)
        selected = [
            item
            for item in batches
            if (owner_scope is None or item.owner_scope is owner_scope)
            and (owner_qq is None or item.owner_qq == owner_qq)
        ]
        items = []
        for batch in selected:
            members = await self._repository.quarantine_batch_items(batch.batch_id)
            items.append(
                {
                    "batch_id": batch.batch_id,
                    "batch_type": batch.batch_type,
                    "owner_scope": batch.owner_scope.value,
                    "owner_qq": batch.owner_qq,
                    "actor_id": batch.actor_id,
                    "process_instance_id": batch.process_instance_id,
                    "state": batch.state.value,
                    "revision": batch.revision,
                    "created_at": batch.created_at.isoformat(),
                    "updated_at": batch.updated_at.isoformat(),
                    "correlation_id": batch.correlation_id,
                    "items": [
                        {
                            "artifact_id": member.artifact_id,
                            "sequence": member.sequence,
                            "state": member.state.value,
                            "error_code": member.error_code,
                        }
                        for member in members
                    ],
                }
            )
        return {"items": items, "count": len(items), "paths_exposed": False}

    async def _latest_decision(
        self,
        *,
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        current_instance_only: bool,
    ) -> ReadinessDecision | None:
        decisions = await self._repository.list_readiness_decisions(
            bot_qq=self.bot_qq,
            process_instance_id=(self.process_instance_id if current_instance_only else None),
            profile=profile,
            capability_scope=capability_scope,
            limit=500,
        )
        return decisions[0] if decisions else None

    def _summary_item(
        self,
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        decision: ReadinessDecision | None,
    ) -> dict[str, Any]:
        if decision is None:
            return {
                "profile": profile.value,
                "capability_scope": capability_scope.value,
                "evaluated": False,
                "status": "not_evaluated",
                "blocker_codes": [],
                "warning_codes": [],
            }
        detail = self._decision_dto(decision, current_instance=True, include_probes=False)
        return {
            key: detail[key]
            for key in (
                "profile",
                "capability_scope",
                "evaluated",
                "status",
                "recorded_status",
                "revision",
                "evaluated_at",
                "evidence_expires_at",
                "stale_probe_count",
                "blocker_codes",
                "warning_codes",
                "correlation_id",
            )
        }

    def _decision_dto(
        self,
        decision: ReadinessDecision,
        *,
        current_instance: bool,
        include_probes: bool,
    ) -> dict[str, Any]:
        now = self._now()
        stale = tuple(item for item in decision.probes if now > item.freshness.expires_at)
        blockers = [
            {
                "code": item.code.value,
                "probe_code": item.probe_code,
                "capability_scope": item.capability_scope.value,
                "safe_detail": item.safe_detail,
            }
            for item in decision.blockers
        ]
        known = {(item["code"], item["probe_code"]) for item in blockers}
        if not current_instance:
            blockers.append(
                {
                    "code": "process_instance_mismatch",
                    "probe_code": "process.instance",
                    "capability_scope": decision.capability_scope.value,
                    "safe_detail": "historical process evidence cannot authorize this instance",
                }
            )
        for probe in stale:
            if ("evidence_stale", probe.probe_code) not in known:
                blockers.append(
                    {
                        "code": "evidence_stale",
                        "probe_code": probe.probe_code,
                        "capability_scope": probe.capability_scope.value,
                        "safe_detail": "persisted evidence is past its absolute expiry",
                    }
                )
        effective_status = (
            ReadinessDecisionStatus.BLOCKED.value
            if stale or decision.status is ReadinessDecisionStatus.BLOCKED or not current_instance
            else ReadinessDecisionStatus.PASSED.value
        )
        result: dict[str, Any] = {
            "decision_id": decision.decision_id,
            "bot_qq": decision.bot_qq,
            "process_instance_id": decision.process_instance_id,
            "current_instance": current_instance,
            "profile": decision.profile.value,
            "capability_scope": decision.capability_scope.value,
            "evaluated": True,
            "status": effective_status,
            "recorded_status": decision.status.value,
            "revision": decision.revision,
            "evaluated_at": decision.evaluated_at.isoformat(),
            "evidence_expires_at": (
                min(item.freshness.expires_at for item in decision.probes).isoformat()
                if decision.probes
                else None
            ),
            "stale_probe_count": len(stale),
            "blockers": blockers,
            "blocker_codes": sorted({item["code"] for item in blockers}),
            "warnings": [
                {
                    "code": item.code.value,
                    "probe_code": item.probe_code,
                    "capability_scope": item.capability_scope.value,
                    "safe_detail": item.safe_detail,
                }
                for item in decision.warnings
            ],
            "warning_codes": sorted({item.code.value for item in decision.warnings}),
            "correlation_id": decision.correlation_id,
        }
        if include_probes:
            result["probes"] = [
                {
                    "probe_code": item.probe_code,
                    "status": (
                        ProbeStatus.STALE.value
                        if now > item.freshness.expires_at
                        else item.status.value
                    ),
                    "recorded_status": item.status.value,
                    "capability_scope": item.capability_scope.value,
                    "observed_at": item.freshness.observed_at.isoformat(),
                    "expires_at": item.freshness.expires_at.isoformat(),
                    "fresh": now <= item.freshness.expires_at,
                    "source": item.source,
                    "source_revision": item.source_revision,
                    "safe_detail": item.safe_detail,
                    "remediation_code": item.remediation_code,
                }
                for item in decision.probes
            ]
        return result

    @staticmethod
    def _artifact_dto(artifact: ManagedArtifact) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type.value,
            "owner_scope": artifact.owner_scope.value,
            "owner_qq": artifact.owner_qq,
            "display_name": PurePosixPath(artifact.relative_path.replace("\\", "/")).name,
            "size_bytes": artifact.size_bytes,
            "created_at": artifact.created_at.isoformat(),
            "verification_state": artifact.verification_state.value,
            "verification_revision": artifact.verification_revision,
            "reference_state": artifact.reference_state.value,
            "reference_revision": artifact.reference_revision,
            "retention_state": artifact.retention_state.value,
            "revision": artifact.revision,
        }

    @staticmethod
    def _filter_artifacts(
        artifacts: Sequence[ManagedArtifact],
        *,
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        artifact_type: ManagedArtifactType | None = None,
        verification_state: ArtifactVerificationState | None = None,
        reference_state: ArtifactReferenceState | None = None,
        retention_state: ArtifactRetentionState | None = None,
    ) -> list[ManagedArtifact]:
        return sorted(
            (
                item
                for item in artifacts
                if (owner_scope is None or item.owner_scope is owner_scope)
                and (owner_qq is None or item.owner_qq == owner_qq)
                and (artifact_type is None or item.artifact_type is artifact_type)
                and (verification_state is None or item.verification_state is verification_state)
                and (reference_state is None or item.reference_state is reference_state)
                and (retention_state is None or item.retention_state is retention_state)
            ),
            key=lambda item: (item.created_at, item.artifact_id),
            reverse=True,
        )

    def _validate_scope(
        self,
        *,
        bot_qq: str | None,
        process_instance_id: str | None,
        require_current_instance: bool,
    ) -> None:
        if bot_qq is not None and bot_qq != self.bot_qq:
            raise OperationalApiError(
                "bot_scope_mismatch",
                "requested bot is outside the current operational scope",
                status_code=403,
            )
        if require_current_instance and not process_instance_id:
            raise OperationalApiError(
                "process_instance_required",
                "current process instance identity is required",
                status_code=422,
            )
        if process_instance_id is not None and process_instance_id != self.process_instance_id:
            raise OperationalApiError(
                "process_instance_mismatch",
                "requested process instance is not current",
                status_code=409,
            )

    @staticmethod
    def _validate_profile_scope(
        profile: ReadinessProfile, capability_scope: CapabilityScope
    ) -> None:
        valid = (
            (
                profile is ReadinessProfile.LOCAL_START
                and capability_scope is CapabilityScope.LOCAL_RUNTIME
            )
            or (
                profile is ReadinessProfile.OFFLINE_SHADOW
                and capability_scope is CapabilityScope.OFFLINE_INFERENCE
            )
            or (
                profile is ReadinessProfile.CONTROLLED_REAL_EFFECT
                and capability_scope
                not in {CapabilityScope.LOCAL_RUNTIME, CapabilityScope.OFFLINE_INFERENCE}
            )
        )
        if not valid:
            raise OperationalApiError(
                "profile_scope_mismatch",
                "capability scope does not belong to the requested readiness profile",
                status_code=422,
            )

    @staticmethod
    def _validate_owner_filter(
        owner_scope: ArtifactOwnerScope | None, owner_qq: str | None
    ) -> None:
        if owner_scope is ArtifactOwnerScope.USER and (owner_qq is None or not owner_qq.isdigit()):
            raise OperationalApiError(
                "user_owner_required",
                "user artifact scope requires an exact numeric owner QQ",
                status_code=422,
            )
        if owner_scope is ArtifactOwnerScope.SYSTEM and owner_qq is not None:
            raise OperationalApiError(
                "system_owner_forbidden",
                "system artifact scope cannot include a user QQ",
                status_code=422,
            )
        if owner_scope is None and owner_qq is not None:
            raise OperationalApiError(
                "owner_scope_required",
                "owner scope is required when filtering by owner QQ",
                status_code=422,
            )

    async def _current_owner(self) -> str:
        owner = await self._repository.current_owner_qq() or self._configured_owner_qq
        if not owner.isdigit():
            raise OperationalApiError(
                "owner_authorization_required",
                "current owner identity is unavailable",
                status_code=403,
            )
        return owner

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise RuntimeError("operational API clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _bearer_token(authorization: str) -> str:
        return authorization[7:] if authorization.lower().startswith("bearer ") else ""


def retention_error_as_operational(exc: RetentionError) -> OperationalApiError:
    status = 422 if exc.code in {"invalid_token", "owner_scope_required"} else 409
    return OperationalApiError(exc.code, str(exc), status_code=status)
