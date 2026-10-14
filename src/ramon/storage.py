"""Operational storage contract used during the SQLite-to-PostgreSQL migration.

The contract deliberately models Ramon behaviour (idempotent bar/sample/trade
writes and immutable telemetry) instead of exposing SQL dialects to services.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .core import Bar, Market


class OperationalRepository(Protocol):
    """Persistence boundary for execution-adjacent telemetry; never authorizes trades."""
    def persist_market(self, market: Market) -> int: ...
    def load_bars(self, symbol: str, timeframe: str = "M15") -> tuple[tuple[Bar, ...], tuple[int, ...]]: ...
    def persist_decision(self, values: dict[str, object]) -> bool: ...
    def persist_trade(self, values: dict[str, object]) -> None: ...
    def persist_shadow_vote(self, values: dict[str, object]) -> bool: ...
    def persist_replay_input(self, values: dict[str, object]) -> bool: ...


@dataclass(frozen=True)
class PostgresOperationalRepository:
    """Native PostgreSQL implementation; all operations are transaction-scoped."""
    dsn: str

    def persist_market(self, market: Market) -> int:
        from .postgres_operational import persist_market
        return persist_market(self.dsn, market)

    def load_bars(self, symbol: str, timeframe: str = "M15") -> tuple[tuple[Bar, ...], tuple[int, ...]]:
        from .postgres_operational import load_bars
        return load_bars(self.dsn, symbol, timeframe)

    def persist_decision(self, values: dict[str, object]) -> bool:
        from .postgres_operational import persist_decision_sample
        return persist_decision_sample(self.dsn, values)

    def persist_trade(self, values: dict[str, object]) -> None:
        from .postgres_operational import persist_trade_outcome
        persist_trade_outcome(self.dsn, values)

    def persist_shadow_vote(self, values: dict[str, object]) -> bool:
        from .postgres_operational import persist_shadow_vote
        return persist_shadow_vote(self.dsn, values)

    def persist_replay_input(self, values: dict[str, object]) -> bool:
        from .postgres_operational import persist_replay_input
        return persist_replay_input(self.dsn, values)
