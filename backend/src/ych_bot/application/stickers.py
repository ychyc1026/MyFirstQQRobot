"""Local sticker library lookup. Never sends QQ; never generates images."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from ych_bot.domain.stickers import ALLOWED_STICKER_TAGS

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class StickerLibrary:
    def __init__(self, root: Path, *, rng: random.Random | None = None) -> None:
        self.root = root
        self._rng = rng or random.Random()

    def catalog(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for tag in sorted(ALLOWED_STICKER_TAGS):
            files = self._files_for(tag)
            items.append({"tag": tag, "count": len(files), "files": [path.name for path in files]})
        return {
            "root": str(self.root),
            "outbound_attached": False,
            "ready_tags": sum(1 for item in items if item["count"] > 0),
            "items": items,
        }

    def resolve(self, tag: str) -> Path | None:
        files = self._files_for(tag)
        if not files:
            return None
        return self._rng.choice(files)

    def _files_for(self, tag: str) -> list[Path]:
        if tag not in ALLOWED_STICKER_TAGS:
            return []
        folder = self.root / tag
        if not folder.is_dir():
            return []
        return sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
