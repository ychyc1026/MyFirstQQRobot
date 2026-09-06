"""Filesystem anchors that stay correct regardless of how deep a test file sits."""

from __future__ import annotations

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = TESTS_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT.parent
SOURCE_ROOT = BACKEND_ROOT / "src" / "ych_bot"
FIXTURES_ROOT = TESTS_ROOT / "fixtures"
