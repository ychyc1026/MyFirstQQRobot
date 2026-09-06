from __future__ import annotations

import pytest
from ych_bot.domain import (
    ContextBudgetError,
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    apply_context_budget,
)


def section(
    section_id: str,
    source: ContextSourceClass,
    content: str,
) -> ContextSection:
    return ContextSection(
        section_id=section_id,
        source_class=source,
        scope=ContextScope.GLOBAL,
        content=content,
        policy_decision=ContextPolicyDecision.ALLOWED,
        policy_reason="test",
    )


def context(*sections: ContextSection) -> ContextManifest:
    return ContextManifest(
        run_id="run-budget",
        conversation_key="2000000002:private:10001",
        sections=sections,
    )


def mandatory_context() -> ContextManifest:
    return context(
        section("identity", ContextSourceClass.CORE_IDENTITY, "identity"),
        section("authorization", ContextSourceClass.AUTHORIZATION, "authorized"),
        section("conversation", ContextSourceClass.CONVERSATION, "current message"),
    )


def test_budgeting_is_deterministic_and_uses_fixed_priority() -> None:
    unordered = context(
        section("history", ContextSourceClass.HISTORY, "old"),
        section("conversation", ContextSourceClass.CONVERSATION, "current"),
        section("identity", ContextSourceClass.CORE_IDENTITY, "identity"),
        section("persona", ContextSourceClass.PERSONA, "persona"),
        section("authorization", ContextSourceClass.AUTHORIZATION, "authorized"),
    )

    first = apply_context_budget(unordered, budget_chars=2_000)
    second = apply_context_budget(unordered, budget_chars=2_000)

    assert first == second
    assert [item.section_id for item in first.manifest.sections] == [
        "identity",
        "authorization",
        "conversation",
        "persona",
        "history",
    ]
    assert first.used_chars == len(first.rendered_content)
    assert first.used_chars <= first.budget_chars


def test_optional_context_is_truncated_after_mandatory_sections() -> None:
    mandatory = apply_context_budget(mandatory_context(), budget_chars=2_000)
    oversized = context(
        *mandatory_context().sections,
        section("persona", ContextSourceClass.PERSONA, "P" * 500),
        section("knowledge", ContextSourceClass.KNOWLEDGE, "K" * 500),
    )

    result = apply_context_budget(
        oversized,
        budget_chars=mandatory.used_chars + 120,
    )
    sections = {item.section_id: item for item in result.manifest.sections}

    assert sections["identity"].content == "identity"
    assert sections["conversation"].content == "current message"
    assert sections["persona"].truncated is True
    assert 0 < len(sections["persona"].content) < 500
    assert sections["persona"].omitted_chars == 500 - len(sections["persona"].content)
    assert sections["knowledge"].content == ""
    assert sections["knowledge"].truncated is True
    assert sections["knowledge"].omitted_chars == 500
    assert result.omitted_chars == (
        sections["persona"].omitted_chars + sections["knowledge"].omitted_chars
    )
    assert result.used_chars <= result.budget_chars


def test_budget_too_small_for_identity_authorization_and_trigger_fails_closed() -> None:
    required = apply_context_budget(mandatory_context(), budget_chars=2_000).used_chars

    with pytest.raises(ContextBudgetError, match="mandatory sections"):
        apply_context_budget(mandatory_context(), budget_chars=required - 1)


def test_denied_sections_are_preserved_but_never_rendered() -> None:
    denied = ContextSection(
        section_id="history",
        source_class=ContextSourceClass.HISTORY,
        scope=ContextScope.USER,
        subject_qq="10001",
        content="",
        policy_decision=ContextPolicyDecision.DENIED,
        record_ids=(),
        policy_reason="history_mode_deny",
    )
    result = apply_context_budget(
        context(*mandatory_context().sections, denied),
        budget_chars=2_000,
    )

    assert result.manifest.sections[-1] == denied
    assert "history" not in result.rendered_content
