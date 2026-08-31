"""Position-state boundary shared by the API and Oracle workers."""

from __future__ import annotations

import os
from typing import Protocol

from app import store


class PositionStateStore(Protocol):
    def list_active(self) -> list[dict]: ...
    def save(self, payload: dict) -> None: ...
    def confirm_next_fill(self, symbol: str) -> dict: ...


class SqlitePositionStateStore:
    """Current durable implementation; PostgreSQL can implement the same contract."""

    def list_active(self) -> list[dict]:
        return store.list_positions()

    def save(self, payload: dict) -> None:
        store.save_position(payload)

    def confirm_next_fill(self, symbol: str) -> dict:
        return store.confirm_next_fill(symbol)


def build_position_state_store() -> PositionStateStore:
    mode = os.getenv("POSITION_STORE", "sqlite").strip().lower()
    if mode == "sqlite":
        return SqlitePositionStateStore()
    if mode in {"postgres", "postgresql"}:
        raise RuntimeError(
            "PostgreSQL adapter is not bundled yet; implement PositionStateStore and set POSITION_STORE after migration"
        )
    raise ValueError(f"Unknown POSITION_STORE={mode}")
