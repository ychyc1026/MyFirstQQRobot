"""Validated result contracts for the two independent knowledge-processing purposes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .persona import DocumentPurpose
from .system_identity import LOCKED_IDENTITY_FIELDS


class KnowledgeDraftError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeDraft:
    purpose: DocumentPurpose
    payload: dict[str, Any]


def validate_knowledge_draft(
    purpose: DocumentPurpose,
    payload: dict[str, Any],
) -> KnowledgeDraft:
    if purpose is DocumentPurpose.USER_UNDERSTANDING:
        summary = payload.get("summary")
        evidence = payload.get("evidence", [])
        if not isinstance(summary, dict) or not summary:
            raise KnowledgeDraftError("user understanding requires a non-empty summary object")
        if not isinstance(evidence, list):
            raise KnowledgeDraftError("user understanding evidence must be a list")
        normalized = {"summary": summary, "evidence": evidence}
        return KnowledgeDraft(purpose=purpose, payload=normalized)

    traits = payload.get("traits")
    evidence = payload.get("evidence", [])
    if not isinstance(traits, dict) or not traits:
        raise KnowledgeDraftError("persona design requires a non-empty traits object")
    if not isinstance(evidence, list):
        raise KnowledgeDraftError("persona design evidence must be a list")
    forbidden = sorted(_find_locked_fields(traits))
    if forbidden:
        raise KnowledgeDraftError(
            "persona draft attempts to override locked identity fields: " + ", ".join(forbidden)
        )
    return KnowledgeDraft(purpose=purpose, payload={"traits": traits, "evidence": evidence})


def _find_locked_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in LOCKED_IDENTITY_FIELDS:
                found.add(normalized)
            found.update(_find_locked_fields(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_locked_fields(child))
    elif isinstance(value, str):
        lowered = value.lower()
        protected_terms = {
            "creator",
            "developer",
            "owner",
            "创造者",
            "开发者",
            "主人",
            "2000000001",
            "2000000002",
        }
        found.update(term for term in protected_terms if term in lowered)
    return found
