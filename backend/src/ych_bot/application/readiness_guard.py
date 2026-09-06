"""Fail-closed readiness enforcement at real external-effect boundaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.readiness import (
    CapabilityScope,
    ReadinessDecision,
    ReadinessDecisionStatus,
    ReadinessProfile,
)

from .readiness import ReadinessService


class ReadinessBlockedError(RuntimeError):
    """A real external effect was rejected before its adapter was called."""

    def __init__(self, capability_scope: CapabilityScope, blocker_codes: tuple[str, ...]) -> None:
        self.capability_scope = capability_scope
        self.blocker_codes = blocker_codes or ("readiness_blocked",)
        super().__init__(
            f"{capability_scope.value} blocked by readiness: {','.join(self.blocker_codes)}"
        )


class ReadinessGuard:
    """Evaluate current-process readiness without an HTTP call to the application itself."""

    def __init__(
        self,
        repository: Any,
        *,
        bot_qq: str,
        process_instance_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not bot_qq.isdigit() or not process_instance_id:
            raise ValueError("readiness guard requires an exact bot and process instance")
        self._repository = repository
        self._bot_qq = bot_qq
        self._process_instance_id = process_instance_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._service: ReadinessService | None = None

    def bind(self, service: ReadinessService) -> None:
        if self._service is not None and self._service is not service:
            raise RuntimeError("readiness guard is already bound")
        if (
            service.bot_qq != self._bot_qq
            or service.process_instance_id != self._process_instance_id
        ):
            raise ValueError("readiness service does not match the guarded bot and process")
        self._service = service

    async def require(
        self,
        capability_scope: CapabilityScope,
        *,
        operation: str,
        operation_id: str = "",
    ) -> ReadinessDecision:
        service = self._service
        if service is None:
            await self._record_block(
                capability_scope,
                operation=operation,
                operation_id=operation_id,
                blocker_codes=("readiness_guard_unbound",),
                decision=None,
            )
            raise ReadinessBlockedError(capability_scope, ("readiness_guard_unbound",))
        try:
            decision = await service.evaluate(
                ReadinessProfile.CONTROLLED_REAL_EFFECT,
                capability_scope=capability_scope,
            )
        except Exception:
            await self._record_block(
                capability_scope,
                operation=operation,
                operation_id=operation_id,
                blocker_codes=("readiness_evaluation_failed",),
                decision=None,
            )
            raise ReadinessBlockedError(
                capability_scope, ("readiness_evaluation_failed",)
            ) from None

        blocker_codes = self._validate_decision(service, decision, capability_scope)
        if blocker_codes:
            await self._record_block(
                capability_scope,
                operation=operation,
                operation_id=operation_id,
                blocker_codes=blocker_codes,
                decision=decision,
            )
            raise ReadinessBlockedError(capability_scope, blocker_codes)
        return decision

    def _validate_decision(
        self,
        service: ReadinessService,
        decision: ReadinessDecision,
        capability_scope: CapabilityScope,
    ) -> tuple[str, ...]:
        blockers = {item.code.value for item in decision.blockers}
        expected_scope_hash = hashlib.sha256(
            ":".join(
                (
                    self._bot_qq,
                    self._process_instance_id,
                    ReadinessProfile.CONTROLLED_REAL_EFFECT.value,
                    capability_scope.value,
                )
            ).encode("utf-8")
        ).hexdigest()
        if (
            decision.bot_qq != self._bot_qq
            or decision.process_instance_id != self._process_instance_id
            or decision.profile is not ReadinessProfile.CONTROLLED_REAL_EFFECT
            or decision.capability_scope is not capability_scope
            or decision.scope_hash != expected_scope_hash
        ):
            blockers.add("readiness_scope_mismatch")
        required = set(service.required_probe_codes(decision.profile, capability_scope))
        observed = {item.probe_code for item in decision.probes}
        if not required.issubset(observed):
            blockers.add("required_probe_unknown")
        now = self._clock().astimezone(UTC)
        if any(now > item.freshness.expires_at for item in decision.probes):
            blockers.add("evidence_stale")
        if decision.status is not ReadinessDecisionStatus.PASSED:
            blockers.add("readiness_blocked")
        return tuple(sorted(blockers))

    async def _record_block(
        self,
        capability_scope: CapabilityScope,
        *,
        operation: str,
        operation_id: str,
        blocker_codes: tuple[str, ...],
        decision: ReadinessDecision | None,
    ) -> None:
        safe_operation = (
            operation
            if operation.replace("_", "").replace(".", "").isalnum()
            else "external_effect"
        )
        operation_hash = (
            hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
            if operation_id
            else "none"
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "scope": capability_scope.value,
                    "operation": safe_operation,
                    "blockers": blocker_codes,
                    "process": self._process_instance_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        details = {
            "bot_qq": self._bot_qq,
            "process_instance_id": self._process_instance_id,
            "profile": ReadinessProfile.CONTROLLED_REAL_EFFECT.value,
            "capability_scope": capability_scope.value,
            "operation": safe_operation,
            "operation_id_hash": operation_hash,
            "blocker_codes": list(blocker_codes),
            "decision_id": decision.decision_id if decision else None,
            "decision_revision": decision.revision if decision else None,
            "correlation_id": decision.correlation_id if decision else None,
            "network_attempted": False,
            "automatic_retry": False,
        }
        await self._repository.record_system_audit(
            "readiness.external_effect_blocked",
            f"{capability_scope.value}:{fingerprint}",
            details,
        )
        with suppress(Exception):
            await self._repository.record_dashboard_only_owner_report(
                severity="warning",
                category="operational_readiness",
                title="External effect blocked by readiness",
                body=(
                    f"capability={capability_scope.value}; operation={safe_operation}; "
                    f"blockers={','.join(blocker_codes)}"
                ),
                related_type="operational_readiness",
                related_id=f"{capability_scope.value}:{fingerprint}",
                dedupe_key=f"readiness:block:{fingerprint}",
                now=self._clock(),
            )
