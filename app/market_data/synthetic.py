from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.market_data.base import MarketDataProvider
from app.market_data.models import CandleEvent, MarketEvent, OptionQuote, QuoteEvent


class SyntheticMarketDataProvider(MarketDataProvider):
    """Deterministic normalized event replay for demos and tests."""

    def __init__(self, events: Sequence[MarketEvent], delay_seconds: float = 0):
        self.events = list(events)
        self.delay_seconds = delay_seconds
        self._quotes: dict[str, QuoteEvent] = {}
        self._candles: dict[str, list[CandleEvent]] = {}
        self.closed = False

    async def get_quote(self, symbol: str) -> QuoteEvent | None:
        return self._quotes.get(symbol.upper())

    async def get_recent_candles(
        self, symbol: str, interval_seconds: int, limit: int = 100
    ) -> Sequence[CandleEvent]:
        values = [
            c
            for c in self._candles.get(symbol.upper(), [])
            if c.interval_seconds == interval_seconds
        ]
        return values[-limit:]

    async def get_option_chain(self, symbol: str) -> Sequence[OptionQuote]:
        return []

    async def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]:
        wanted = {s.upper() for s in symbols}
        for event in self.events:
            if self.closed:
                return
            if event.symbol.upper() not in wanted:
                continue
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if isinstance(event, QuoteEvent):
                self._quotes[event.symbol.upper()] = event
            elif isinstance(event, CandleEvent):
                self._candles.setdefault(event.symbol.upper(), []).append(event)
            yield event

    async def close(self) -> None:
        self.closed = True
