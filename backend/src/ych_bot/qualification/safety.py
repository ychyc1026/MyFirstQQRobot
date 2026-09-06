"""Pre-attempt checks for controlled live qualification."""

from __future__ import annotations

from dataclasses import dataclass

from ych_bot.domain.model_qualification import QualificationReasonCode, QualificationRouteRevision
from ych_bot.infrastructure.models import ModelProtectionSnapshot


@dataclass(frozen=True, slots=True)
class QualificationLiveGuards:
    emergency_paused: bool
    current_revision: QualificationRouteRevision | None
    protection: ModelProtectionSnapshot | None


def evaluate_qualification_pre_attempt(
    *,
    run_revision: QualificationRouteRevision,
    guards: QualificationLiveGuards,
) -> QualificationReasonCode | None:
    if guards.emergency_paused:
        return QualificationReasonCode.EMERGENCY_PAUSE
    current = guards.current_revision
    if current is None:
        return QualificationReasonCode.STALE_ROUTE_REVISION
    if current.capability is not run_revision.capability:
        return QualificationReasonCode.STALE_ROUTE_REVISION
    if current.price_catalog_revision != run_revision.price_catalog_revision:
        return QualificationReasonCode.STALE_PRICE_CATALOG
    if current.fingerprint != run_revision.fingerprint:
        return QualificationReasonCode.STALE_ROUTE_REVISION
    snapshot = guards.protection
    if snapshot is None:
        return QualificationReasonCode.QUOTA_EXHAUSTED
    if snapshot.state in {"open", "half_open"}:
        return QualificationReasonCode.CIRCUIT_OPEN
    if snapshot.requests_used >= snapshot.request_limit:
        return QualificationReasonCode.QUOTA_EXHAUSTED
    if snapshot.token_limit is not None and snapshot.tokens_used >= snapshot.token_limit:
        return QualificationReasonCode.QUOTA_EXHAUSTED
    return None
