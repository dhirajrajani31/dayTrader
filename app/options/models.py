from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OptionCandidate(BaseModel):
    symbol: str
    streamer_symbol: str
    underlying_symbol: str
    expiration: datetime
    days_to_expiration: int
    strike: float
    call_put: str
    shares_per_contract: int = 100
    quote_timestamp: datetime | None = None
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    iv: float | None = None
    open_interest: int | None = None
    volume: int | None = None

    @property
    def mid(self) -> float | None:
        return None if self.bid is None or self.ask is None else (self.bid + self.ask) / 2

    @property
    def spread(self) -> float | None:
        return None if self.bid is None or self.ask is None else self.ask - self.bid

    @property
    def spread_pct(self) -> float | None:
        mid = self.mid
        spread = self.spread
        return None if mid is None or mid <= 0 or spread is None else spread / mid

    @property
    def simulated_entry_fill(self) -> float | None:
        """A conservative long-option shadow fill at the displayed ask."""
        return self.ask
