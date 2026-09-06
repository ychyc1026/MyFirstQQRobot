"""Explicit readiness boundary fakes for isolated unit tests."""

from __future__ import annotations

from typing import Any

from ych_bot.application import ReadinessBlockedError
from ych_bot.domain.readiness import CapabilityScope


class AllowReadinessGuard:
    def __init__(self) -> None:
        self.calls: list[tuple[CapabilityScope, str, str]] = []

    async def require(
        self,
        capability_scope: CapabilityScope,
        *,
        operation: str,
        operation_id: str = "",
    ) -> Any:
        self.calls.append((capability_scope, operation, operation_id))
        return None


class BlockReadinessGuard(AllowReadinessGuard):
    def __init__(self, code: str = "synthetic_block") -> None:
        super().__init__()
        self.code = code

    async def require(
        self,
        capability_scope: CapabilityScope,
        *,
        operation: str,
        operation_id: str = "",
    ) -> Any:
        await super().require(
            capability_scope,
            operation=operation,
            operation_id=operation_id,
        )
        raise ReadinessBlockedError(capability_scope, (self.code,))


ALLOW_READINESS = AllowReadinessGuard()
