"""Shared pytest configuration: tier markers and cross-test isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
TIERS = (
    "domain",
    "adapters",
    "repository",
    "services",
    "dashboard",
    "qualification",
    "acceptance",
)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    for item in items:
        try:
            relative = Path(str(item.fspath)).resolve().relative_to(TESTS_ROOT)
        except ValueError:
            continue
        tier = relative.parts[0]
        if tier in TIERS:
            item.add_marker(getattr(pytest.mark, tier))


@pytest.fixture(autouse=True)
def reset_shared_readiness_guard():
    """The permissive guard is a module-level singleton, so its log must not leak."""
    from _support.readiness import ALLOW_READINESS

    ALLOW_READINESS.calls.clear()
    yield
    ALLOW_READINESS.calls.clear()
