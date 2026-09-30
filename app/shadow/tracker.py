from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.strategy.models import Direction, Signal


class ShadowTradeRecord(BaseModel):
    symbol: str
    direction: Direction
    opened_at: datetime
    strategy_name: str
    strategy_version: str
    entry_underlying: float
    invalidation_underlying: float | None
    target_1: float | None
    target_2: float | None
    source_watchlist_tag: str | None = None
    entry_context: dict = Field(default_factory=dict)
    prices_after: dict[str, float] = Field(default_factory=dict)
    maximum_favorable_excursion: float = 0
    maximum_adverse_excursion: float = 0
    stop_hit: bool = False
    target_1_hit: bool = False
    target_2_hit: bool = False
    time_to_target_1_seconds: float | None = None
    time_to_target_2_seconds: float | None = None

    @property
    def estimated_r_multiple(self) -> float | None:
        if self.invalidation_underlying is None:
            return None
        risk = abs(self.entry_underlying - self.invalidation_underlying)
        return None if risk == 0 else self.maximum_favorable_excursion / risk


class ShadowTracker:
    checkpoints = (5, 15, 30, 60)

    def __init__(self) -> None:
        self.active: dict[str, ShadowTradeRecord] = {}

    def open(
        self, signal: Signal, context: dict | None = None, source_tag: str | None = None
    ) -> ShadowTradeRecord:
        record = ShadowTradeRecord(
            symbol=signal.symbol,
            direction=signal.direction,
            opened_at=signal.timestamp,
            strategy_name=signal.strategy_name,
            strategy_version=signal.strategy_version,
            entry_underlying=signal.price,
            invalidation_underlying=signal.invalidation,
            target_1=signal.target_1,
            target_2=signal.target_2,
            source_watchlist_tag=source_tag,
            entry_context=context or {},
        )
        self.active[signal.symbol] = record
        return record

    def update(self, symbol: str, timestamp: datetime, price: float) -> ShadowTradeRecord | None:
        trade = self.active.get(symbol)
        if trade is None or timestamp < trade.opened_at:
            return trade
        sign = 1 if trade.direction == Direction.BULLISH else -1
        move = sign * (price - trade.entry_underlying)
        trade.maximum_favorable_excursion = max(trade.maximum_favorable_excursion, move)
        trade.maximum_adverse_excursion = max(trade.maximum_adverse_excursion, -move)
        elapsed = timestamp - trade.opened_at
        for minute in self.checkpoints:
            key = f"{minute}m"
            if elapsed >= timedelta(minutes=minute) and key not in trade.prices_after:
                trade.prices_after[key] = price
        if trade.invalidation_underlying is not None:
            trade.stop_hit |= (
                price <= trade.invalidation_underlying
                if sign == 1
                else price >= trade.invalidation_underlying
            )
        if trade.target_1 is not None and not trade.target_1_hit:
            hit = price >= trade.target_1 if sign == 1 else price <= trade.target_1
            if hit:
                trade.target_1_hit = True
                trade.time_to_target_1_seconds = elapsed.total_seconds()
        if trade.target_2 is not None and not trade.target_2_hit:
            hit = price >= trade.target_2 if sign == 1 else price <= trade.target_2
            if hit:
                trade.target_2_hit = True
                trade.time_to_target_2_seconds = elapsed.total_seconds()
        return trade
