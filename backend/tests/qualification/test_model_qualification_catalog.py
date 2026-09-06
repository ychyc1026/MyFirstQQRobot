from pathlib import Path

import pytest
from ych_bot.qualification.catalog import (
    load_bundled_qualification_catalog,
    load_qualification_catalog,
)


def test_bundled_catalog_is_repository_owned_and_synthetic() -> None:
    catalog = load_bundled_qualification_catalog()
    assert catalog.source_kind == "repository_owned"
    assert catalog.version
    for fixture in catalog.fixtures:
        assert fixture.sensitivity == "synthetic_public"
        assert fixture.source_kind == "repository_owned"


def test_catalog_rejects_arbitrary_filesystem_and_upload_sources(tmp_path: Path) -> None:
    leaked = tmp_path / "diary.txt"
    leaked.write_text("private diary", encoding="utf-8")
    with pytest.raises(ValueError, match="repository"):
        load_qualification_catalog(source_path=str(leaked))
    with pytest.raises(ValueError, match="repository"):
        load_qualification_catalog(source_kind="uploaded_document")
    with pytest.raises(ValueError, match="repository"):
        load_qualification_catalog(database_message_id="stored-qq-message-1")
    with pytest.raises(ValueError, match="repository|synthetic"):
        load_qualification_catalog(user_qq="123456789")


def test_catalog_resolve_rejects_unknown_and_production_selectors() -> None:
    catalog = load_bundled_qualification_catalog()
    with pytest.raises(ValueError, match="unknown fixture"):
        catalog.resolve_fixture("chat-not-in-catalog")
    with pytest.raises(ValueError, match="synthetic"):
        catalog.select_input({"message_id": "m-1", "path": "C:/secrets.txt"})
