"""Shared durable pause state for background workers."""

from __future__ import annotations

from ych_bot.infrastructure.database import SQLiteRepository


class DurablePauseMixin:
    _pause_worker_name: str
    _paused: bool
    _pause_state_loaded: bool

    def _pause_repository(self) -> SQLiteRepository:
        repository = getattr(self, "_repository", None) or getattr(self, "repository", None)
        if repository is None:
            raise RuntimeError("worker repository is not configured")
        return repository

    def _ensure_pause_state(self) -> None:
        if getattr(self, "_pause_state_loaded", False):
            return
        self._paused = self._pause_repository().worker_paused_sync(self._pause_worker_name)
        self._pause_state_loaded = True

    def _set_paused(self, paused: bool) -> bool:
        self._ensure_pause_state()
        changed = self._paused != paused
        self._paused = paused
        if changed:
            self._pause_repository().set_worker_paused_sync(
                self._pause_worker_name,
                paused,
                updated_by="operator",
            )
        return changed
