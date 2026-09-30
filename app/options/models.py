from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OptionCandidate(BaseModel):
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

    @property
    def mid(self) -> float | None:
        return None if self.bid is None or self.ask is None else (self.bid + self.ask) / 2

    @property
    def spread(self) -> float | None:
        return None if self.bid is None or self.ask is None else self.ask - self.bid
