from datetime import UTC, datetime
from decimal import Decimal

import pytest
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCeilings,
    QualificationReasonCode,
    can_confirm_controlled_live,
)
from ych_bot.qualification.prices import (
    load_bundled_qualification_price_catalog,
    load_qualification_price_catalog,
    plan_controlled_live_cost,
)


def _token_ceilings(**overrides: object) -> QualificationCeilings:
    values: dict[str, object] = {
        "max_requests": 2,
        "max_input_tokens": 1000,
        "max_output_tokens": 500,
        "max_images": None,
        "conservative_max_cost": None,
    }
    values.update(overrides)
    return QualificationCeilings(**values)  # type: ignore[arg-type]


def test_bundled_price_catalog_records_official_sources_without_credentials() -> None:
    catalog = load_bundled_qualification_price_catalog()
    serialized = catalog.as_sanitized_dict()

    assert catalog.revision == "siliconflow-2026-09-05"
    assert catalog.currency == "CNY"
    assert catalog.protocol == "openai_compatible"
    assert catalog.sanitized_base_host == "api.siliconflow.cn"
    assert catalog.retrieved_at == datetime(2026, 9, 5, 6, 20, tzinfo=UTC)
    assert any("siliconflow.cn/pricing" in source for source in catalog.sources)
    assert "Qwen/Qwen3.5-35B-A3B" in catalog.model_identifiers
    assert catalog.proposed_model(QualificationCapability.CHAT) == "Qwen/Qwen3.5-35B-A3B"
    assert catalog.proposed_model(QualificationCapability.STATS) == "Qwen/Qwen3.5-35B-A3B"
    assert catalog.proposed_model(QualificationCapability.VISION) == "zai-org/GLM-4.5V"
    assert catalog.proposed_model(QualificationCapability.IMAGE) == "Kwai-Kolors/Kolors"
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    assert all("sk-" not in source for source in catalog.sources)


def test_price_catalog_rejects_external_or_client_supplied_prices() -> None:
    with pytest.raises(ValueError, match="repository-owned"):
        load_qualification_price_catalog(source_path="C:/prices.json")
    with pytest.raises(ValueError, match="repository-owned"):
        load_qualification_price_catalog(raw_price={"model": "x", "input": "0.01"})


def test_official_cny_chat_plan_uses_highest_published_tier_and_request_ceiling() -> None:
    catalog = load_bundled_qualification_price_catalog()
    plan = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="Qwen/Qwen3.5-35B-A3B",
        ceilings=_token_ceilings(),
    )

    # 2 * ((1000/1e6)*1.60 + (500/1e6)*12.80) = 0.016
    assert plan.allowed is True
    assert plan.reason_code is None
    assert plan.planned_max_cost == Decimal("0.0160")
    assert plan.price_catalog_revision == "siliconflow-2026-09-05"
    assert plan.billing_basis == "official_cny"


def test_usd_listing_only_and_missing_bounds_block_live_confirmation() -> None:
    catalog = load_bundled_qualification_price_catalog()
    usd_only = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="Qwen/Qwen2.5-7B-Instruct",
        ceilings=_token_ceilings(),
    )
    unknown = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="unknown/not-listed",
        ceilings=_token_ceilings(),
    )
    unbounded = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="Qwen/Qwen3.5-35B-A3B",
        ceilings=_token_ceilings(max_output_tokens=None),
    )
    stale = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="Qwen/Qwen3.5-35B-A3B",
        ceilings=_token_ceilings(),
        expected_revision="siliconflow-old",
    )

    assert usd_only.allowed is False
    assert usd_only.reason_code is QualificationReasonCode.COST_UNBOUNDED
    assert unknown.reason_code is QualificationReasonCode.COST_UNBOUNDED
    assert unbounded.reason_code is QualificationReasonCode.COST_UNBOUNDED
    assert stale.reason_code is QualificationReasonCode.STALE_PRICE_CATALOG


def test_image_plan_uses_official_cny_per_image_and_allows_established_zero() -> None:
    catalog = load_bundled_qualification_price_catalog()
    free = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.IMAGE,
        model_identifier="Kwai-Kolors/Kolors",
        ceilings=QualificationCeilings(
            max_requests=3,
            max_input_tokens=None,
            max_output_tokens=None,
            max_images=1,
            conservative_max_cost=None,
        ),
    )
    paid = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.IMAGE,
        model_identifier="Qwen/Qwen-Image",
        ceilings=QualificationCeilings(
            max_requests=2,
            max_input_tokens=None,
            max_output_tokens=None,
            max_images=1,
            conservative_max_cost=None,
        ),
    )
    missing_images = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.IMAGE,
        model_identifier="Kwai-Kolors/Kolors",
        ceilings=_token_ceilings(max_images=None),
    )

    turbo = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.IMAGE,
        model_identifier="Tongyi-MAI/Z-Image-Turbo",
        ceilings=QualificationCeilings(
            max_requests=7,
            max_input_tokens=None,
            max_output_tokens=None,
            max_images=7,
            conservative_max_cost=None,
        ),
    )
    assert free.allowed is True
    assert free.planned_max_cost == Decimal("0.0000")
    assert paid.planned_max_cost == Decimal("0.3000")
    assert turbo.allowed is True
    assert turbo.planned_max_cost == Decimal("0.7000")
    assert turbo.billing_basis == "official_cny"
    assert missing_images.allowed is False
    assert missing_images.reason_code is QualificationReasonCode.COST_UNBOUNDED


def test_planner_ignores_client_supplied_raw_price() -> None:
    catalog = load_bundled_qualification_price_catalog()
    plan = plan_controlled_live_cost(
        catalog,
        capability=QualificationCapability.CHAT,
        model_identifier="Qwen/Qwen3.5-35B-A3B",
        ceilings=_token_ceilings(conservative_max_cost=Decimal("0.0001")),
        client_unit_price=Decimal("0.0000001"),
    )

    assert plan.planned_max_cost == Decimal("0.0160")
    assert plan.planned_max_cost != Decimal("0.0001")


def test_established_zero_cost_can_confirm_when_image_ceiling_exists() -> None:
    from ych_bot.domain.model_qualification import (
        QualificationExecutionMode,
        QualificationPreview,
        QualificationPreviewState,
        QualificationRouteRevision,
    )
    from ych_bot.qualification.catalog import load_bundled_qualification_catalog

    suite = load_bundled_qualification_catalog().suite("image-shadow-v1")
    preview = QualificationPreview(
        preview_id="preview-free-image",
        confirmation_handle_hash="d" * 64,
        actor_id="admin",
        process_instance_id="proc",
        capability=QualificationCapability.IMAGE,
        route_revision=QualificationRouteRevision(
            capability=QualificationCapability.IMAGE,
            provider_protocol="openai_compatible",
            sanitized_base_host="api.siliconflow.cn",
            model_identifier="Kwai-Kolors/Kolors",
            generation_settings=(("response_format", "b64_json"),),
            suite_version=suite.version,
            price_catalog_revision="siliconflow-2026-09-05",
            protection_policy_revision="image-protection-v1",
        ),
        suite=suite,
        policy_revision="image-protection-v1",
        fixture_ids=("image-form-001",),
        ceilings=QualificationCeilings(
            max_requests=1,
            max_input_tokens=None,
            max_output_tokens=None,
            max_images=1,
            conservative_max_cost=Decimal("0"),
        ),
        execution_mode=QualificationExecutionMode.CONTROLLED_LIVE,
        expires_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        state=QualificationPreviewState.READY,
    )

    assert can_confirm_controlled_live(preview, price_evidence_present=True, now=preview.expires_at)
