from ych_bot.domain.model_qualification import QualificationCapability
from ych_bot.qualification.catalog import load_bundled_qualification_catalog

REQUIRED_CHAT_FIXTURES = {
    "chat-style-001",
    "chat-identity-001",
    "chat-impersonation-001",
    "chat-injection-001",
    "chat-identity-mutation-001",
    "chat-structured-001",
    "chat-empty-001",
    "chat-oversize-001",
    "chat-style-bounds-001",
}


def test_chat_suite_covers_required_synthetic_cases() -> None:
    catalog = load_bundled_qualification_catalog()
    suite = catalog.suite("chat-shadow-v1")
    fixtures = {
        item.fixture_id: item for item in catalog.fixtures_for(QualificationCapability.CHAT)
    }

    assert suite.capability is QualificationCapability.CHAT
    assert suite.version == catalog.version
    assert set(suite.fixture_ids) >= REQUIRED_CHAT_FIXTURES
    assert set(fixtures) >= REQUIRED_CHAT_FIXTURES
    assert fixtures["chat-identity-001"].blocking_check_codes == ("creator_identity",)
    assert fixtures["chat-impersonation-001"].blocking_check_codes == ("authority",)
    assert fixtures["chat-injection-001"].blocking_check_codes == ("prompt_injection",)
    assert fixtures["chat-identity-mutation-001"].blocking_check_codes == ("forbidden_field",)
    for fixture in fixtures.values():
        assert fixture.sensitivity == "synthetic_public"
        assert fixture.input_kind == "chat_text"
        assert catalog.case_input(fixture.fixture_id)["text"]
