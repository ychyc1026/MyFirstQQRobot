"""Operator-only display labels for QQ numbers."""

from __future__ import annotations

import re
from enum import StrEnum

_LABEL_PATTERN = re.compile(r"^[\S](?:.*[\S])?$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class OperatorSubjectKind(StrEnum):
    USER = "user"
    GROUP = "group"


class OperatorLabelError(ValueError):
    pass


def normalize_subject_id(subject_id: str) -> str:
    value = subject_id.strip()
    if not value.isdigit():
        raise OperatorLabelError("subject id must be digits only")
    return value


def normalize_label(label: str | None) -> str:
    value = (label or "").strip()
    if not value:
        return ""
    if _CONTROL_CHARS.search(value) or "\n" in value or "\r" in value:
        raise OperatorLabelError("label must not contain control characters")
    if len(value) > 32:
        raise OperatorLabelError("label must be at most 32 characters")
    if not _LABEL_PATTERN.match(value):
        raise OperatorLabelError("label must be visible text")
    return value
