from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from app.market_data.models import CandleEvent, MarketEvent, OptionQuote, QuoteEvent


class MarketDataError(RuntimeError):
    """Recoverable provider error; the strategy loop must continue safely."""


class MarketDataProvider(ABC):
    """Broker-neutral, read-only market-data contract. No order methods belong here."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> QuoteEvent | None: ...

    @abstractmethod
    async def get_recent_candles(
        self, symbol: str, interval_seconds: int, limit: int = 100
    ) -> Sequence[CandleEvent]: ...

    @abstractmethod
    async def get_option_chain(self, symbol: str) -> Sequence[OptionQuote]: ...

    async def get_option_quote(self, symbol: str) -> QuoteEvent | None:
        """Return a current option quote when supported by the provider."""
        return None

    async def warm_up(
        self,
        symbols: Sequence[str],
        *,
        start: datetime,
        timeout_seconds: float = 20,
    ) -> dict[str, list[CandleEvent]]:
        """Load verified historical candles when the provider supports them."""
        return {symbol.upper(): [] for symbol in symbols}

    @abstractmethod
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]: ...

    @abstractmethod
    async def close(self) -> None: ...
