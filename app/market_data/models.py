from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionPhase(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR_MARKET = "REGULAR_MARKET"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STALE = "STALE"


class MarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    timestamp: datetime

    @model_validator(mode="after")
    def aware_timestamp(self) -> MarketEvent:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("market timestamps must be timezone-aware")
        return self


class QuoteEvent(MarketEvent):
    bid: float | None = None
    ask: float | None = None
    last: float | None = None

    @property
    def spread(self) -> float | None:
        return None if self.bid is None or self.ask is None else self.ask - self.bid


class TradeEvent(MarketEvent):
    price: float
    size: float = 0


class CandleEvent(MarketEvent):
    interval_seconds: int = Field(gt=0)
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_ohlc(self) -> CandleEvent:
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close, self.high
        ):
            raise ValueError("invalid OHLC values")
        return self


class OptionQuote(BaseModel):
    symbol: str
    expiration: datetime
    strike: float
    call_put: str
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    iv: float | None = None
    open_interest: int | None = None
    volume: int | None = None
