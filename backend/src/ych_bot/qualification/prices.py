"""Versioned official price evidence for controlled live qualification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_UP, Decimal
from importlib.resources import files
from typing import Any

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCeilings,
    QualificationReasonCode,
)
from ych_bot.domain.readiness import require_aware_utc

_BUNDLED_REVISION = "siliconflow-2026-09-05"
_FORBIDDEN_SOURCE_KEYS = (
    "source_path",
    "path",
    "raw_price",
    "client_price",
    "uploaded_document",
)
_MILLION = Decimal("1000000")
_COST_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class QualificationPriceEntry:
    model_identifier: str
    capabilities: tuple[QualificationCapability, ...]
    endpoint_kind: str
    billing_basis: str
    input_cny_per_million: Decimal | None
    output_cny_per_million: Decimal | None
    image_cny: Decimal | None
    notes: str = ""

    def supports(self, capability: QualificationCapability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class QualificationPriceCatalog:
    revision: str
    provider: str
    currency: str
    protocol: str
    sanitized_base_host: str
    retrieved_at: datetime
    sources: tuple[str, ...]
    entries: tuple[QualificationPriceEntry, ...]
    proposed_routes: tuple[tuple[QualificationCapability, str], ...]

    @property
    def model_identifiers(self) -> frozenset[str]:
        return frozenset(item.model_identifier for item in self.entries)

    def proposed_model(self, capability: QualificationCapability) -> str:
        for item_capability, model_identifier in self.proposed_routes:
            if item_capability is capability:
                return model_identifier
        raise ValueError("no proposed route for capability")

    def entry(
        self, model_identifier: str, capability: QualificationCapability
    ) -> QualificationPriceEntry | None:
        for item in self.entries:
            if item.model_identifier == model_identifier and item.supports(capability):
                return item
        return None

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "provider": self.provider,
            "currency": self.currency,
            "protocol": self.protocol,
            "sanitized_base_host": self.sanitized_base_host,
            "retrieved_at": self.retrieved_at.isoformat(),
            "sources": list(self.sources),
            "models": [item.model_identifier for item in self.entries],
        }


@dataclass(frozen=True, slots=True)
class QualificationCostPlan:
    allowed: bool
    planned_max_cost: Decimal | None
    price_catalog_revision: str
    billing_basis: str | None
    reason_code: QualificationReasonCode | None = None


def load_bundled_qualification_price_catalog() -> QualificationPriceCatalog:
    payload = json.loads(
        (files("ych_bot.qualification.price_catalogs") / "siliconflow_2026_09_05.json").read_text(
            encoding="utf-8"
        )
    )
    if str(payload.get("revision")) != _BUNDLED_REVISION:
        raise ValueError("bundled price catalog revision does not match the loader")
    return _catalog_from_payload(payload)


def load_qualification_price_catalog(**kwargs: object) -> QualificationPriceCatalog:
    if kwargs:
        if any(key in _FORBIDDEN_SOURCE_KEYS and kwargs[key] for key in kwargs):
            raise ValueError("qualification price catalogs must be repository-owned")
        raise ValueError("qualification price catalogs must be repository-owned")
    return load_bundled_qualification_price_catalog()


def plan_controlled_live_cost(
    catalog: QualificationPriceCatalog,
    *,
    capability: QualificationCapability,
    model_identifier: str,
    ceilings: QualificationCeilings,
    expected_revision: str | None = None,
    client_unit_price: Decimal | None = None,
) -> QualificationCostPlan:
    del client_unit_price
    if expected_revision is not None and expected_revision != catalog.revision:
        return QualificationCostPlan(
            allowed=False,
            planned_max_cost=None,
            price_catalog_revision=catalog.revision,
            billing_basis=None,
            reason_code=QualificationReasonCode.STALE_PRICE_CATALOG,
        )
    if ceilings.max_requests < 1:
        return _unbounded(catalog.revision)
    entry = catalog.entry(model_identifier, capability)
    if entry is None or entry.billing_basis != "official_cny":
        return _unbounded(catalog.revision)
    if capability is QualificationCapability.IMAGE:
        if ceilings.max_images is None or ceilings.max_images < 1 or entry.image_cny is None:
            return _unbounded(catalog.revision, entry.billing_basis)
        planned = _quantize(
            Decimal(min(ceilings.max_requests, ceilings.max_images)) * entry.image_cny
        )
        return QualificationCostPlan(
            allowed=True,
            planned_max_cost=planned,
            price_catalog_revision=catalog.revision,
            billing_basis=entry.billing_basis,
        )
    if (
        ceilings.max_input_tokens is None
        or ceilings.max_output_tokens is None
        or ceilings.max_input_tokens < 1
        or ceilings.max_output_tokens < 1
        or entry.input_cny_per_million is None
        or entry.output_cny_per_million is None
    ):
        return _unbounded(catalog.revision, entry.billing_basis)
    per_request = (Decimal(ceilings.max_input_tokens) / _MILLION) * entry.input_cny_per_million + (
        Decimal(ceilings.max_output_tokens) / _MILLION
    ) * entry.output_cny_per_million
    planned = _quantize(Decimal(ceilings.max_requests) * per_request)
    return QualificationCostPlan(
        allowed=True,
        planned_max_cost=planned,
        price_catalog_revision=catalog.revision,
        billing_basis=entry.billing_basis,
    )


def _unbounded(revision: str, billing_basis: str | None = None) -> QualificationCostPlan:
    return QualificationCostPlan(
        allowed=False,
        planned_max_cost=None,
        price_catalog_revision=revision,
        billing_basis=billing_basis,
        reason_code=QualificationReasonCode.COST_UNBOUNDED,
    )


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_COST_QUANTUM, rounding=ROUND_UP)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _catalog_from_payload(payload: dict[str, Any]) -> QualificationPriceCatalog:
    entries = tuple(
        QualificationPriceEntry(
            model_identifier=str(item["model_identifier"]),
            capabilities=tuple(QualificationCapability(code) for code in item["capabilities"]),
            endpoint_kind=str(item["endpoint_kind"]),
            billing_basis=str(item["billing_basis"]),
            input_cny_per_million=_optional_decimal(item.get("input_cny_per_million")),
            output_cny_per_million=_optional_decimal(item.get("output_cny_per_million")),
            image_cny=_optional_decimal(item.get("image_cny")),
            notes=str(item.get("notes") or ""),
        )
        for item in payload["entries"]
    )
    proposed = tuple(
        (QualificationCapability(item["capability"]), str(item["model_identifier"]))
        for item in payload["proposed_routes"]
    )
    return QualificationPriceCatalog(
        revision=str(payload["revision"]),
        provider=str(payload["provider"]),
        currency=str(payload["currency"]),
        protocol=str(payload["protocol"]),
        sanitized_base_host=str(payload["sanitized_base_host"]),
        retrieved_at=require_aware_utc(
            datetime.fromisoformat(str(payload["retrieved_at"])), "retrieved_at"
        ),
        sources=tuple(str(item) for item in payload["sources"]),
        entries=entries,
        proposed_routes=proposed,
    )
