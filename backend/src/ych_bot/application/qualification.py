"""Authenticated preview and single-use confirmation for qualification runs."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCeilings,
    QualificationDecision,
    QualificationExecutionMode,
    QualificationPreview,
    QualificationPreviewState,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
    can_confirm_controlled_live,
    sanitize_qualification_host,
)
from ych_bot.qualification.catalog import QualificationCatalog
from ych_bot.qualification.prices import QualificationPriceCatalog, plan_controlled_live_cost

_PREVIEW_TTL = timedelta(minutes=10)
_EFFECT_ISOLATION = "no_qq_no_outbox_no_qzone"
_DEFAULT_INPUT_TOKENS = 2000
_DEFAULT_OUTPUT_TOKENS = 800
_ALLOWED_PREVIEW_FIELDS = frozenset(
    {
        "capability",
        "fixture_ids",
        "max_input_tokens",
        "max_output_tokens",
        "max_images",
        "passed",
        "decision",
    }
)
_ALLOWED_CONFIRM_FIELDS = frozenset(
    {"confirmation_handle", "idempotency_key", "passed", "decision"}
)
_PRICE_OVERRIDE_FIELDS = frozenset({"raw_price", "client_unit_price", "unit_price"})
_PRODUCTION_INPUT_FIELDS = frozenset(
    {
        "source_path",
        "path",
        "user_qq",
        "database_message_id",
        "uploaded_document",
        "messages",
        "prompt",
        "provider_payload",
        "api_key",
        "authorization",
    }
)


class QualificationPlanningError(RuntimeError):
    def __init__(self, reason_code: QualificationReasonCode, message: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code.value)


@dataclass(frozen=True, slots=True)
class QualificationPreviewOffer:
    preview_id: str
    confirmation_handle: str
    capability: QualificationCapability
    model_identifier: str
    fixture_ids: tuple[str, ...]
    fixture_count: int
    max_requests: int
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_images: int | None
    planned_max_cost: Decimal
    conservative_max_cost: Decimal
    currency: str
    expires_at: datetime
    effect_isolation: str
    price_catalog_revision: str

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "capability": self.capability.value,
            "model_identifier": self.model_identifier,
            "fixture_ids": list(self.fixture_ids),
            "fixture_count": self.fixture_count,
            "max_requests": self.max_requests,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_images": self.max_images,
            "planned_max_cost": str(self.planned_max_cost),
            "conservative_max_cost": str(self.conservative_max_cost),
            "currency": self.currency,
            "expires_at": self.expires_at.isoformat(),
            "effect_isolation": self.effect_isolation,
            "price_catalog_revision": self.price_catalog_revision,
        }


class QualificationPlanningService:
    def __init__(
        self,
        repository: Any,
        *,
        process_instance_id: str,
        fixture_catalog: QualificationCatalog,
        price_catalog: QualificationPriceCatalog,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not process_instance_id:
            raise ValueError("qualification planning requires a process instance")
        self._repository = repository
        self._process_instance_id = process_instance_id
        self._fixture_catalog = fixture_catalog
        self._price_catalog = price_catalog
        self._clock = clock or (lambda: datetime.now(UTC))

    async def preview_controlled_live(
        self,
        *,
        actor_id: str,
        capability: QualificationCapability,
        route_revision: QualificationRouteRevision,
        fixture_ids: tuple[str, ...],
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_images: int | None = None,
        client_unit_price: object | None = None,
    ) -> QualificationPreviewOffer:
        if not actor_id:
            raise QualificationPlanningError(QualificationReasonCode.DEFAULT_DENIED)
        if client_unit_price is not None:
            raise QualificationPlanningError(QualificationReasonCode.COST_UNBOUNDED)
        if capability is not route_revision.capability:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_SCOPE_MISMATCH)
        suite_id = f"{capability.value}-shadow-v1"
        suite = self._fixture_catalog.suite(suite_id)
        if (
            any(fixture_id not in suite.fixture_ids for fixture_id in fixture_ids)
            or not fixture_ids
        ):
            raise QualificationPlanningError(QualificationReasonCode.PRODUCTION_INPUT_REJECTED)
        ceilings = QualificationCeilings(
            max_requests=len(fixture_ids),
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_images=max_images,
            conservative_max_cost=None,
        )
        plan = plan_controlled_live_cost(
            self._price_catalog,
            capability=capability,
            model_identifier=route_revision.model_identifier,
            ceilings=ceilings,
            expected_revision=route_revision.price_catalog_revision,
        )
        if not plan.allowed or plan.planned_max_cost is None:
            raise QualificationPlanningError(
                plan.reason_code or QualificationReasonCode.COST_UNBOUNDED
            )
        bounded = QualificationCeilings(
            max_requests=ceilings.max_requests,
            max_input_tokens=ceilings.max_input_tokens,
            max_output_tokens=ceilings.max_output_tokens,
            max_images=ceilings.max_images,
            conservative_max_cost=plan.planned_max_cost,
        )
        now = self._clock()
        handle = secrets.token_hex(32)
        preview = QualificationPreview(
            preview_id=str(uuid4()),
            confirmation_handle_hash=_handle_hash(handle),
            actor_id=actor_id,
            process_instance_id=self._process_instance_id,
            capability=capability,
            route_revision=route_revision,
            suite=suite,
            policy_revision=route_revision.protection_policy_revision,
            fixture_ids=fixture_ids,
            ceilings=bounded,
            execution_mode=QualificationExecutionMode.CONTROLLED_LIVE,
            expires_at=now + _PREVIEW_TTL,
            state=QualificationPreviewState.READY,
        )
        if not can_confirm_controlled_live(preview, price_evidence_present=True, now=now):
            raise QualificationPlanningError(QualificationReasonCode.COST_UNBOUNDED)
        await self._repository.save_qualification_route_revision(route_revision, now=now)
        stored = await self._repository.create_qualification_preview(preview, now=now)
        return QualificationPreviewOffer(
            preview_id=stored.preview_id,
            confirmation_handle=handle,
            capability=capability,
            model_identifier=route_revision.model_identifier,
            fixture_ids=fixture_ids,
            fixture_count=len(fixture_ids),
            max_requests=bounded.max_requests,
            max_input_tokens=bounded.max_input_tokens,
            max_output_tokens=bounded.max_output_tokens,
            max_images=bounded.max_images,
            planned_max_cost=plan.planned_max_cost,
            conservative_max_cost=plan.planned_max_cost,
            currency=self._price_catalog.currency,
            expires_at=stored.expires_at,
            effect_isolation=_EFFECT_ISOLATION,
            price_catalog_revision=plan.price_catalog_revision,
        )

    async def confirm_controlled_live(
        self,
        *,
        actor_id: str,
        confirmation_handle: str,
        idempotency_key: str,
    ) -> QualificationRun:
        if not actor_id or not confirmation_handle or not idempotency_key:
            raise QualificationPlanningError(QualificationReasonCode.DEFAULT_DENIED)
        now = self._clock()
        handle_hash = _handle_hash(confirmation_handle)
        preview = await self._repository.qualification_preview_by_handle_hash(handle_hash)
        if preview is None:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_REUSED)
        if preview.actor_id != actor_id:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_ACTOR_MISMATCH)
        if preview.process_instance_id != self._process_instance_id:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_PROCESS_MISMATCH)
        if preview.state_at(now) is QualificationPreviewState.EXPIRED:
            raise QualificationPlanningError(QualificationReasonCode.PREVIEW_EXPIRED)
        if preview.state is not QualificationPreviewState.READY:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_REUSED)
        if not can_confirm_controlled_live(preview, price_evidence_present=True, now=now):
            raise QualificationPlanningError(QualificationReasonCode.COST_UNBOUNDED)
        consumed = await self._repository.consume_qualification_preview(
            confirmation_handle_hash=handle_hash,
            actor_id=actor_id,
            process_instance_id=self._process_instance_id,
            route_fingerprint=preview.route_revision.fingerprint,
            suite_id=preview.suite.suite_id,
            suite_version=preview.suite.version,
            policy_revision=preview.policy_revision,
            now=now,
        )
        if consumed is None:
            raise QualificationPlanningError(QualificationReasonCode.CONFIRMATION_REUSED)
        return await self._repository.create_qualification_run(
            QualificationRun(
                run_id=str(uuid4()),
                preview_id=consumed.preview_id,
                actor_id=actor_id,
                process_instance_id=self._process_instance_id,
                capability=consumed.capability,
                route_revision=consumed.route_revision,
                suite=consumed.suite,
                execution_mode=QualificationExecutionMode.CONTROLLED_LIVE,
                state=QualificationRunState.PREPARED,
                idempotency_key=idempotency_key,
                created_at=now,
                correlation_id=str(uuid4()),
            ),
            now=now,
        )


def reject_client_owned_qualification_fields(
    payload: Mapping[str, Any],
    *,
    allowed: frozenset[str],
) -> None:
    extra = {key for key in payload if key not in allowed}
    if extra & _PRICE_OVERRIDE_FIELDS:
        raise QualificationPlanningError(QualificationReasonCode.COST_UNBOUNDED)
    if extra & _PRODUCTION_INPUT_FIELDS:
        raise QualificationPlanningError(QualificationReasonCode.PRODUCTION_INPUT_REJECTED)


def route_revision_from_settings(
    settings: Any,
    capability: QualificationCapability,
    *,
    suite_version: str,
    price_catalog_revision: str,
) -> QualificationRouteRevision:
    prefix = capability.value
    model = str(getattr(settings, f"{prefix}_model", "") or "")
    protocol = str(getattr(settings, f"{prefix}_api_protocol", "disabled") or "disabled")
    base = str(getattr(settings, f"{prefix}_api_base", "") or "")
    if protocol != "openai_compatible" or not model or not base:
        return QualificationRouteRevision(
            capability=capability,
            provider_protocol="unconfigured",
            sanitized_base_host="unconfigured.local",
            model_identifier=model or "unconfigured",
            generation_settings=(("mode", "denied"),),
            suite_version=suite_version,
            price_catalog_revision=price_catalog_revision,
            protection_policy_revision=f"{capability.value}-protection-v1",
        )
    try:
        host = sanitize_qualification_host(base)
    except ValueError:
        host = "unconfigured.local"
    if capability is QualificationCapability.IMAGE:
        response_format = str(getattr(settings, "image_response_format", "b64_json") or "b64_json")
        generation_settings = (("response_format", response_format),)
    else:
        generation_settings = (("temperature", "0.2"),)
    return QualificationRouteRevision(
        capability=capability,
        provider_protocol="openai_compatible",
        sanitized_base_host=host,
        model_identifier=model,
        generation_settings=generation_settings,
        suite_version=suite_version,
        price_catalog_revision=price_catalog_revision,
        protection_policy_revision=f"{capability.value}-protection-v1",
    )


def _run_dto(run: QualificationRun, *, reason_code: str | None = None) -> dict[str, Any]:
    payload = {
        "run_id": run.run_id,
        "preview_id": run.preview_id,
        "capability": run.capability.value,
        "state": run.state.value,
        "execution_mode": run.execution_mode.value,
        "suite_id": run.suite.suite_id,
        "suite_version": run.suite.version,
        "model_identifier": run.route_revision.model_identifier,
        "sanitized_base_host": run.route_revision.sanitized_base_host,
        "correlation_id": run.correlation_id,
        "actor_id": run.actor_id,
        "created_at": run.created_at.isoformat(),
        "activates_production": False,
    }
    if reason_code is not None:
        payload["reason_code"] = reason_code
    return payload


class QualificationApiService:
    def __init__(
        self,
        repository: Any,
        *,
        planning: QualificationPlanningService,
        settings: Any,
        fixture_catalog: QualificationCatalog,
        price_catalog: QualificationPriceCatalog,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._planning = planning
        self._settings = settings
        self._fixture_catalog = fixture_catalog
        self._price_catalog = price_catalog
        self._clock = clock or (lambda: datetime.now(UTC))

    def suite_metadata(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "suite_id": suite.suite_id,
                    "capability": suite.capability.value,
                    "version": suite.version,
                    "fixture_ids": list(suite.fixture_ids),
                    "fixture_count": len(suite.fixture_ids),
                    "blocking_check_codes": list(suite.blocking_check_codes),
                    "source_kind": "repository_owned",
                    "sensitivity": "synthetic_public",
                }
                for suite in self._fixture_catalog.suites
            ]
        }

    def current_routes(self) -> dict[str, Any]:
        return {
            "items": [self._route_summary(capability) for capability in QualificationCapability]
        }

    async def current_decisions(self) -> dict[str, Any]:
        now = self._clock()
        items = []
        for capability in QualificationCapability:
            current = self._current_revision(capability)
            await self._repository.save_qualification_route_revision(current, now=now)
            decision = await self._repository.refresh_stale_qualification_decisions(
                current_revision=current,
                current_suite_version=current.suite_version,
                now=now,
            )
            items.append(self._decision_dto(decision.against_current(current), current))
        return {"items": items}

    async def owner_status_projection(self) -> dict[str, Any]:
        decisions = await self.current_decisions()
        return {
            "can_start_paid_run": False,
            "items": decisions["items"],
        }

    async def preview_controlled_live(
        self,
        *,
        actor_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        reject_client_owned_qualification_fields(payload, allowed=_ALLOWED_PREVIEW_FIELDS)
        capability = QualificationCapability(str(payload["capability"]))
        suite = self._fixture_catalog.suite(f"{capability.value}-shadow-v1")
        requested = payload.get("fixture_ids")
        fixture_ids = tuple(str(item) for item in requested) if requested else suite.fixture_ids
        max_images = payload.get("max_images")
        if capability is QualificationCapability.IMAGE and max_images is None:
            max_images = len(fixture_ids)
        max_input = payload.get("max_input_tokens")
        max_output = payload.get("max_output_tokens")
        if capability is not QualificationCapability.IMAGE:
            if max_input is None:
                max_input = _DEFAULT_INPUT_TOKENS
            if max_output is None:
                max_output = _DEFAULT_OUTPUT_TOKENS
        offer = await self._planning.preview_controlled_live(
            actor_id=actor_id,
            capability=capability,
            route_revision=self._current_revision(capability),
            fixture_ids=fixture_ids,
            max_input_tokens=max_input,
            max_output_tokens=max_output,
            max_images=max_images,
        )
        return {
            **offer.as_sanitized_dict(),
            "confirmation_handle": offer.confirmation_handle,
            "activates_production": False,
        }

    async def confirm_controlled_live(
        self,
        *,
        actor_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        reject_client_owned_qualification_fields(payload, allowed=_ALLOWED_CONFIRM_FIELDS)
        run = await self._planning.confirm_controlled_live(
            actor_id=actor_id,
            confirmation_handle=str(payload["confirmation_handle"]),
            idempotency_key=str(payload["idempotency_key"]),
        )
        return _run_dto(run)

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        cancelled = await self._repository.cancel_qualification_run(run_id)
        if cancelled is not None:
            return _run_dto(cancelled, reason_code=QualificationReasonCode.CANCELLED.value)
        existing = await self._repository.qualification_run(run_id)
        if existing is None:
            raise QualificationPlanningError(
                QualificationReasonCode.DEFAULT_DENIED, "run not found"
            )
        raise QualificationPlanningError(QualificationReasonCode.CANCELLED)

    async def list_runs(
        self,
        *,
        capability: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("pagination bounds are invalid")
        selected = None if capability is None else QualificationCapability(capability)
        runs, total = await self._repository.list_qualification_runs(
            capability=selected,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [_run_dto(run) for run in runs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def run_detail(self, run_id: str) -> dict[str, Any] | None:
        run = await self._repository.qualification_run(run_id)
        if run is None:
            return None
        cases = await self._repository.qualification_cases(run_id)
        artifacts = await self._repository.qualification_artifacts(run_id)
        cost = await self._repository.qualification_cost_evidence(run_id)
        return {
            **_run_dto(run),
            "cases": [case.as_sanitized_dict() for case in cases],
            "cost_evidence": cost,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "mime_type": item.mime_type,
                    "size_bytes": item.size_bytes,
                    "pixel_width": item.pixel_width,
                    "pixel_height": item.pixel_height,
                }
                for item in artifacts
            ],
        }

    def _current_revision(self, capability: QualificationCapability) -> QualificationRouteRevision:
        suite = self._fixture_catalog.suite(f"{capability.value}-shadow-v1")
        return route_revision_from_settings(
            self._settings,
            capability,
            suite_version=suite.version,
            price_catalog_revision=self._price_catalog.revision,
        )

    def _route_configured(self, capability: QualificationCapability) -> bool:
        prefix = capability.value
        return (
            str(getattr(self._settings, f"{prefix}_api_protocol", "disabled"))
            == "openai_compatible"
            and bool(getattr(self._settings, f"{prefix}_api_base", ""))
            and bool(getattr(self._settings, f"{prefix}_model", ""))
        )

    def _route_summary(self, capability: QualificationCapability) -> dict[str, Any]:
        revision = self._current_revision(capability)
        return {
            "capability": capability.value,
            "configured": self._route_configured(capability),
            "route_enabled": bool(
                getattr(self._settings, f"{capability.value}_model_enabled", False)
            ),
            "network_enabled": bool(getattr(self._settings, "model_network_enabled", False)),
            "qualified": False,
            "model_identifier": revision.model_identifier,
            "sanitized_base_host": revision.sanitized_base_host,
            "provider_protocol": revision.provider_protocol,
            "suite_version": revision.suite_version,
            "price_catalog_revision": revision.price_catalog_revision,
            "fingerprint": revision.fingerprint,
            "activates_production": False,
        }

    def _decision_dto(
        self,
        decision: QualificationDecision,
        current: QualificationRouteRevision,
    ) -> dict[str, Any]:
        return {
            "capability": decision.capability.value,
            "status": decision.status.value,
            "qualifies": decision.qualifies,
            "activates_production": False,
            "configured": self._route_configured(decision.capability),
            "route_enabled": bool(
                getattr(self._settings, f"{decision.capability.value}_model_enabled", False)
            ),
            "model_identifier": current.model_identifier,
            "sanitized_base_host": current.sanitized_base_host,
            "suite_version": decision.suite_version,
            "blocker_codes": [item.value for item in decision.blocker_codes],
            "advisory_score": None
            if decision.advisory_score is None
            else str(decision.advisory_score),
            "run_id": decision.run_id,
            "evaluated_at": decision.evaluated_at.isoformat(),
            "stale_reason": (
                QualificationReasonCode.STALE_ROUTE_REVISION.value
                if decision.status.value == "stale"
                else None
            ),
        }


def _handle_hash(confirmation_handle: str) -> str:
    return hashlib.sha256(confirmation_handle.encode("utf-8")).hexdigest()
