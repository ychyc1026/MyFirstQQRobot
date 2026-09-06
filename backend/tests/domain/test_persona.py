from ych_bot.domain.models import ConversationKind
from ych_bot.domain.persona import (
    PersonaProfile,
    ProfileScope,
    ProfileSource,
    UserUnderstanding,
    build_persona_context,
)


def profiles() -> tuple[PersonaProfile, ...]:
    return (
        PersonaProfile(
            id="global",
            scope=ProfileScope.GLOBAL,
            scope_id="*",
            source=ProfileSource.MANUAL,
            developer_definition="稳重、简洁",
            traits={},
        ),
        PersonaProfile(
            id="user-a-derived",
            scope=ProfileScope.PRIVATE_USER,
            scope_id="10001",
            source=ProfileSource.DOCUMENT_DERIVED,
            developer_definition="",
            traits={"relationship_style": "老朋友"},
        ),
        PersonaProfile(
            id="user-a-manual",
            scope=ProfileScope.PRIVATE_USER,
            scope_id="10001",
            source=ProfileSource.MANUAL,
            developer_definition="更自然地交流",
            traits={},
        ),
    )


def test_private_context_is_isolated_by_user() -> None:
    context = build_persona_context(
        conversation_kind=ConversationKind.PRIVATE,
        peer_id="10001",
        profiles=profiles(),
        understandings=(
            UserUnderstanding(
                id="understanding-a",
                user_qq="10001",
                summary={"preferences": ["摄影"]},
            ),
            UserUnderstanding(
                id="understanding-b",
                user_qq="10002",
                summary={"preferences": ["音乐"]},
            ),
        ),
    )

    assert context.base_definition == "稳重、简洁"
    assert context.core_identity.creator_name == "维护者"
    assert context.core_identity.creator_qq == "2000000001"
    assert context.core_identity.locked is True
    assert context.private_definition == "更自然地交流"
    assert context.derived_persona == {"relationship_style": "老朋友"}
    assert context.user_context == {"preferences": ["摄影"]}
    assert "understanding-a" not in context.applied_profile_ids


def test_group_context_never_uses_private_persona_or_user_understanding() -> None:
    context = build_persona_context(
        conversation_kind=ConversationKind.GROUP,
        peer_id="10001",
        profiles=profiles(),
        understandings=(
            UserUnderstanding(
                id="understanding-a",
                user_qq="10001",
                summary={"private": "must not leak"},
            ),
        ),
    )

    assert context.base_definition == "稳重、简洁"
    assert context.private_definition is None
    assert context.derived_persona is None
    assert context.user_context is None
    assert context.applied_profile_ids == ("global",)
    assert any(
        "不可被" in directive and "覆盖" in directive for directive in context.core_directives
    )
