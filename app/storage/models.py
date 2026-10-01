from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC wall time in SQLite and always return an aware UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class WatchlistSessionRow(Base):
    __tablename__ = "watchlist_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    symbols: Mapped[list] = mapped_column(JSON)
    source_tag: Mapped[str | None] = mapped_column(String(100))


class MarketFeatureSnapshotRow(Base):
    __tablename__ = "market_feature_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    quality: Mapped[str] = mapped_column(String(16))
    features: Mapped[dict] = mapped_column(JSON)


class StrategyStateRow(Base):
    __tablename__ = "strategy_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(32))
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class StateTransitionRow(Base):
    __tablename__ = "state_transitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    from_state: Mapped[str] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict] = mapped_column(JSON)


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)


class ShadowTradeRow(Base):
    __tablename__ = "shadow_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    entry_underlying: Mapped[float] = mapped_column(Float)
    invalidation_underlying: Mapped[float | None] = mapped_column(Float)
    target_1: Mapped[float | None] = mapped_column(Float)
    target_2: Mapped[float | None] = mapped_column(Float)
    entry_context: Mapped[dict] = mapped_column(JSON)


class ShadowTradeOutcomeRow(Base):
    __tablename__ = "shadow_trade_outcomes"
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_trade_id: Mapped[int] = mapped_column(ForeignKey("shadow_trades.id"), index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict] = mapped_column(JSON)


class ShadowOptionTradeRow(Base):
    __tablename__ = "shadow_option_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_trade_id: Mapped[int] = mapped_column(
        ForeignKey("shadow_trades.id"), unique=True, index=True
    )
    underlying_symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    signal_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    option_symbol: Mapped[str] = mapped_column(String(32), index=True)
    streamer_symbol: Mapped[str] = mapped_column(String(64))
    expiration: Mapped[datetime] = mapped_column(UTCDateTime())
    days_to_expiration: Mapped[int] = mapped_column(Integer)
    strike: Mapped[float] = mapped_column(Float)
    call_put: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    multiplier: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    entry_bid: Mapped[float | None] = mapped_column(Float)
    entry_ask: Mapped[float] = mapped_column(Float)
    entry_mid: Mapped[float | None] = mapped_column(Float)
    entry_fill: Mapped[float] = mapped_column(Float)
    entry_delta: Mapped[float | None] = mapped_column(Float)
    entry_gamma: Mapped[float | None] = mapped_column(Float)
    entry_theta: Mapped[float | None] = mapped_column(Float)
    entry_iv: Mapped[float | None] = mapped_column(Float)
    entry_open_interest: Mapped[int | None] = mapped_column(Integer)
    entry_volume: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True, default="OPEN")
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    exit_reason: Mapped[str | None] = mapped_column(String(64))
    exit_fill: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float)
    realized_return_pct: Mapped[float | None] = mapped_column(Float)
    selection_payload: Mapped[dict] = mapped_column(JSON)


class ShadowOptionMarkRow(Base):
    __tablename__ = "shadow_option_marks"
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_option_trade_id: Mapped[int] = mapped_column(
        ForeignKey("shadow_option_trades.id"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    underlying_price: Mapped[float] = mapped_column(Float)
    option_bid: Mapped[float | None] = mapped_column(Float)
    option_ask: Mapped[float | None] = mapped_column(Float)
    option_mid: Mapped[float | None] = mapped_column(Float)
    liquidation_price: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float)
    unrealized_return_pct: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class ShadowExecutionEventRow(Base):
    __tablename__ = "shadow_execution_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("shadow_trades.id"), index=True
    )
    shadow_option_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("shadow_option_trades.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    stage: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    intended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delay_seconds: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ApplicationEventRow(Base):
    __tablename__ = "application_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
