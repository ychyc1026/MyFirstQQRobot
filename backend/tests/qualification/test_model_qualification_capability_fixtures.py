from ych_bot.domain.model_qualification import QualificationCapability
from ych_bot.qualification.catalog import load_bundled_qualification_catalog

REQUIRED_VISION = {
    "vision-ground-001",
    "vision-uncertainty-001",
    "vision-unsupported-001",
    "vision-bounds-001",
    "vision-privacy-001",
}
REQUIRED_STATS = {
    "stats-schema-001",
    "stats-arithmetic-001",
    "stats-uncertainty-001",
    "stats-bounded-001",
    "stats-separation-001",
}
REQUIRED_IMAGE = {
    "image-form-001",
    "image-mime-001",
    "image-count-001",
    "image-limits-001",
    "image-confine-001",
    "image-review-001",
    "image-cleanup-001",
}


def test_vision_stats_and_image_suites_are_synthetic_and_capability_specific() -> None:
    catalog = load_bundled_qualification_catalog()
    vision = {
        item.fixture_id: item for item in catalog.fixtures_for(QualificationCapability.VISION)
    }
    stats = {item.fixture_id: item for item in catalog.fixtures_for(QualificationCapability.STATS)}
    image = {item.fixture_id: item for item in catalog.fixtures_for(QualificationCapability.IMAGE)}

    assert set(catalog.suite("vision-shadow-v1").fixture_ids) >= REQUIRED_VISION
    assert set(catalog.suite("stats-shadow-v1").fixture_ids) >= REQUIRED_STATS
    assert set(catalog.suite("image-shadow-v1").fixture_ids) >= REQUIRED_IMAGE
    assert set(vision) >= REQUIRED_VISION
    assert set(stats) >= REQUIRED_STATS
    assert set(image) >= REQUIRED_IMAGE
    assert catalog.case_image("vision-ground-001").startswith(b"\x89PNG")
    assert "path" not in catalog.case_input("vision-ground-001")
    assert catalog.case_input("stats-arithmetic-001")["aggregates"]
    assert catalog.case_input("image-form-001")["prompt"]
    for fixture in vision.values():
        assert fixture.input_kind == "vision_image"
        assert fixture.sensitivity == "synthetic_public"
    for fixture in stats.values():
        assert fixture.input_kind == "stats_aggregate"
    for fixture in image.values():
        assert fixture.input_kind == "image_prompt"
        assert "artifact_safety" in fixture.blocking_check_codes
