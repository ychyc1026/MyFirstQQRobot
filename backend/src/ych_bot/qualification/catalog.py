"""Repository-owned synthetic qualification catalogs."""

from __future__ import annotations

from dataclasses import dataclass

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationFixture,
    QualificationSuite,
    reject_non_synthetic_qualification_input,
)
from ych_bot.qualification.fixtures.chat import CHAT_FIXTURES, CHAT_INPUTS, CHAT_SUITE
from ych_bot.qualification.fixtures.image import IMAGE_FIXTURES, IMAGE_INPUTS, IMAGE_SUITE
from ych_bot.qualification.fixtures.stats import STATS_FIXTURES, STATS_INPUTS, STATS_SUITE
from ych_bot.qualification.fixtures.vision import (
    VISION_FIXTURES,
    VISION_IMAGES,
    VISION_INPUTS,
    VISION_SUITE,
)

_FORBIDDEN_SOURCE_KEYS = (
    "source_path",
    "source_kind",
    "database_message_id",
    "user_qq",
    "path",
    "uploaded_document",
)


@dataclass(frozen=True, slots=True)
class QualificationCatalog:
    version: str
    source_kind: str
    suites: tuple[QualificationSuite, ...] = ()
    fixtures: tuple[QualificationFixture, ...] = ()
    inputs: tuple[tuple[str, dict[str, str]], ...] = ()
    images: tuple[tuple[str, bytes], ...] = ()

    def __post_init__(self) -> None:
        if self.source_kind != "repository_owned":
            raise ValueError("qualification catalogs must be repository-owned")
        if not self.version:
            raise ValueError("catalog version is required")
        for fixture in self.fixtures:
            if (
                fixture.sensitivity != "synthetic_public"
                or fixture.source_kind != "repository_owned"
            ):
                raise ValueError("catalog fixtures must be synthetic repository records")

    def suite(self, suite_id: str) -> QualificationSuite:
        for item in self.suites:
            if item.suite_id == suite_id:
                return item
        raise ValueError("unknown suite")

    def fixtures_for(self, capability: QualificationCapability) -> tuple[QualificationFixture, ...]:
        return tuple(item for item in self.fixtures if item.capability is capability)

    def resolve_fixture(self, fixture_id: str) -> QualificationFixture:
        for fixture in self.fixtures:
            if fixture.fixture_id == fixture_id:
                return fixture
        raise ValueError("unknown fixture")

    def case_input(self, fixture_id: str) -> dict[str, str]:
        self.resolve_fixture(fixture_id)
        for stored_id, payload in self.inputs:
            if stored_id == fixture_id:
                return dict(payload)
        raise ValueError("unknown fixture")

    def case_image(self, fixture_id: str) -> bytes:
        self.resolve_fixture(fixture_id)
        for stored_id, payload in self.images:
            if stored_id == fixture_id:
                return payload
        raise ValueError("unknown fixture")

    def select_input(self, payload: dict[str, object]) -> QualificationFixture:
        reject_non_synthetic_qualification_input(payload)
        fixture_id = payload.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError("unknown fixture")
        return self.resolve_fixture(fixture_id)


def load_bundled_qualification_catalog() -> QualificationCatalog:
    return QualificationCatalog(
        version="2026.09.05",
        source_kind="repository_owned",
        suites=(CHAT_SUITE, VISION_SUITE, STATS_SUITE, IMAGE_SUITE),
        fixtures=CHAT_FIXTURES + VISION_FIXTURES + STATS_FIXTURES + IMAGE_FIXTURES,
        inputs=tuple(
            {
                **CHAT_INPUTS,
                **VISION_INPUTS,
                **STATS_INPUTS,
                **IMAGE_INPUTS,
            }.items()
        ),
        images=tuple(VISION_IMAGES.items()),
    )


def load_qualification_catalog(**kwargs: object) -> QualificationCatalog:
    if kwargs:
        if any(key in _FORBIDDEN_SOURCE_KEYS and kwargs[key] for key in kwargs):
            raise ValueError("qualification catalogs must be repository-owned synthetic fixtures")
        raise ValueError("qualification catalogs must be repository-owned")
    return load_bundled_qualification_catalog()
