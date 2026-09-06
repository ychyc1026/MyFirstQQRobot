"""Deterministic character budgeting for typed reply context."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .reply_pipeline import (
    ContextManifest,
    ContextPolicyDecision,
    ContextSection,
    ContextSourceClass,
)


class ContextBudgetError(ValueError):
    """The configured context budget cannot preserve mandatory sections."""


@dataclass(frozen=True, slots=True)
class ContextBudgetResult:
    manifest: ContextManifest
    rendered_content: str
    budget_chars: int
    used_chars: int
    omitted_chars: int


_PRIORITY = {
    ContextSourceClass.CORE_IDENTITY: 0,
    ContextSourceClass.AUTHORIZATION: 1,
    ContextSourceClass.CONVERSATION: 2,
    ContextSourceClass.PERSONA: 3,
    ContextSourceClass.USER_UNDERSTANDING: 4,
    ContextSourceClass.KNOWLEDGE: 4,
    ContextSourceClass.MEMORY: 5,
    ContextSourceClass.HISTORY: 6,
}
_MANDATORY = frozenset(
    {
        ContextSourceClass.CORE_IDENTITY,
        ContextSourceClass.AUTHORIZATION,
        ContextSourceClass.CONVERSATION,
    }
)
_SEPARATOR = "\n\n"


def apply_context_budget(
    manifest: ContextManifest,
    *,
    budget_chars: int,
) -> ContextBudgetResult:
    """Render allowed sections within a stable priority and character budget."""

    if budget_chars < 1:
        raise ContextBudgetError("context budget must be positive")
    ordered = tuple(
        section
        for _, section in sorted(
            enumerate(manifest.sections),
            key=lambda item: (_PRIORITY[item[1].source_class], item[0]),
        )
    )
    mandatory = tuple(
        section
        for section in ordered
        if section.policy_decision is ContextPolicyDecision.ALLOWED
        and section.source_class in _MANDATORY
    )
    mandatory_size = _joined_size(tuple(_render(section, section.content) for section in mandatory))
    if mandatory_size > budget_chars:
        raise ContextBudgetError(
            f"context budget cannot preserve mandatory sections; requires {mandatory_size} chars"
        )

    rendered: list[str] = []
    budgeted: list[ContextSection] = []
    omitted_total = 0
    for section in ordered:
        if section.policy_decision is ContextPolicyDecision.DENIED:
            budgeted.append(section)
            continue
        separator_size = len(_SEPARATOR) if rendered else 0
        available = budget_chars - _joined_size(tuple(rendered)) - separator_size
        full = _render(section, section.content)
        if len(full) <= available:
            rendered.append(full)
            budgeted.append(section)
            continue
        if section.source_class in _MANDATORY:
            raise ContextBudgetError("mandatory context ordering exceeded the reserved budget")

        overhead = len(_render(section, ""))
        included_chars = max(0, min(len(section.content), available - overhead))
        included = section.content[:included_chars]
        omitted = len(section.content) - included_chars
        omitted_total += omitted
        budgeted.append(
            replace(
                section,
                content=included,
                truncated=omitted > 0,
                omitted_chars=omitted,
            )
        )
        if included_chars > 0:
            rendered.append(_render(section, included))

    rendered_content = _SEPARATOR.join(rendered)
    return ContextBudgetResult(
        manifest=ContextManifest(
            run_id=manifest.run_id,
            conversation_key=manifest.conversation_key,
            sections=tuple(budgeted),
        ),
        rendered_content=rendered_content,
        budget_chars=budget_chars,
        used_chars=len(rendered_content),
        omitted_chars=omitted_total,
    )


def render_context_manifest(
    manifest: ContextManifest,
    *,
    exclude_sources: frozenset[ContextSourceClass] = frozenset(),
) -> str:
    """Render already-budgeted allowed context without denied or empty sections."""

    return _SEPARATOR.join(
        _render(section, section.content)
        for section in manifest.sections
        if section.policy_decision is ContextPolicyDecision.ALLOWED
        and section.source_class not in exclude_sources
        and section.content
    )


def _render(section: ContextSection, content: str) -> str:
    return (
        f'<context id="{section.section_id}" source="{section.source_class.value}">\n'
        f"{content}\n</context>"
    )


def _joined_size(items: tuple[str, ...]) -> int:
    if not items:
        return 0
    return sum(len(item) for item in items) + len(_SEPARATOR) * (len(items) - 1)
