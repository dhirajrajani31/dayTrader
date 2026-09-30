from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets

from app.market_data.auth import OAuthTokenManager
from app.market_data.base import MarketDataError, MarketDataProvider
from app.market_data.models import (
    CandleEvent,
    ConnectionState,
    MarketEvent,
    OptionQuote,
    QuoteEvent,
    TradeEvent,
)


class TastytradeMarketDataProvider(MarketDataProvider):
    """Read-only adapter for tastytrade's documented REST and DXLink interfaces.

    It deliberately exposes no account/order client. Option-chain normalization remains isolated
    until its schemas are verified with credentials; unavailable data is returned as an empty
    sequence.
    """

    def __init__(self, auth: OAuthTokenManager | str, stale_after_seconds: int = 30):
        self.auth = (
            auth
            if isinstance(auth, OAuthTokenManager)
            else OAuthTokenManager("https://api.tastyworks.com", access_token=auth)
        )
        if not self.auth.configured:
            raise ValueError("tastytrade OAuth credentials are required")
        self.stale_after_seconds = stale_after_seconds
        self.connection_state = ConnectionState.DISCONNECTED
        self._quotes: dict[str, QuoteEvent] = {}
        self._candles: dict[str, list[CandleEvent]] = {}
        self._closed = False
        self.log = logging.getLogger("tradingpilot.market_data.tastytrade")

    async def get_quote(self, symbol: str) -> QuoteEvent | None:
        symbol = symbol.upper()
        try:
            response = await self.auth.request(
                "GET", "/market-data/by-type", params={"equity": symbol}
            )
            items = response.json().get("data", {}).get("items", [])
            if not items:
                return None
            raw = items[0]
            timestamp = datetime.fromisoformat(raw["updated-at"].replace("Z", "+00:00"))
            event = QuoteEvent(
                symbol=symbol,
                timestamp=timestamp,
                bid=_float_or_none(raw.get("bid")),
                ask=_float_or_none(raw.get("ask")),
                last=_float_or_none(raw.get("last")),
            )
            self._quotes[symbol] = event
            return event
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(f"quote request failed for {symbol}: {exc}") from exc

    async def get_recent_candles(
        self, symbol: str, interval_seconds: int, limit: int = 100
    ) -> Sequence[CandleEvent]:
        return [
            c
            for c in self._candles.get(symbol.upper(), [])
            if c.interval_seconds == interval_seconds
        ][-limit:]

    async def warm_up(
        self,
        symbols: Sequence[str],
        *,
        start: datetime,
        timeout_seconds: float = 20,
    ) -> dict[str, list[CandleEvent]]:
        """Load one-minute DXLink Candle history beginning at `start`.

        DXLink time-series snapshots remain subscribed after the snapshot, so completion is
        detected using a short quiet period bounded by an overall timeout. Duplicate candle
        updates are collapsed by symbol/timestamp.
        """
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("historical warm-up start must be timezone-aware")
        wanted = sorted({symbol.upper() for symbol in symbols})
        result: dict[str, dict[datetime, CandleEvent]] = {symbol: {} for symbol in wanted}
        url, token = await self._stream_credentials()
        try:
            async with websockets.connect(url, ping_interval=None, close_timeout=5) as socket:
                await self._send_setup(
                    socket,
                    token,
                    {
                        "Candle": [
                            "eventType",
                            "eventSymbol",
                            "eventFlags",
                            "index",
                            "time",
                            "sequence",
                            "count",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                        ]
                    },
                )
                add = [
                    {
                        "type": "Candle",
                        "symbol": f"{symbol}{{=1m}}",
                        "fromTime": int(start.timestamp() * 1000),
                    }
                    for symbol in wanted
                ]
                await socket.send(
                    json.dumps(
                        {
                            "type": "FEED_SUBSCRIPTION",
                            "channel": 3,
                            "reset": True,
                            "add": add,
                        }
                    )
                )
                deadline = asyncio.get_running_loop().time() + timeout_seconds
                received = False
                completed: set[str] = set()
                quiet_intervals = 0
                while asyncio.get_running_loop().time() < deadline:
                    remaining = deadline - asyncio.get_running_loop().time()
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=min(1.0, remaining))
                    except TimeoutError:
                        quiet_intervals += 1
                        if received and (completed.issuperset(wanted) or quiet_intervals >= 3):
                            break
                        continue
                    quiet_intervals = 0
                    completed.update(self._candle_snapshot_completions(message))
                    candles = self._parse_candle_message(message)
                    received |= bool(candles)
                    for candle in candles:
                        if candle.symbol in result:
                            result[candle.symbol][candle.timestamp] = candle
        except (OSError, websockets.WebSocketException, json.JSONDecodeError) as exc:
            raise MarketDataError(f"historical candle warm-up failed: {exc}") from exc
        normalized: dict[str, list[CandleEvent]] = {}
        for symbol, indexed in result.items():
            candles = sorted(indexed.values(), key=lambda candle: candle.timestamp)
            normalized[symbol] = candles
            self._candles[symbol] = candles[-10_000:]
        return normalized

    async def get_option_chain(self, symbol: str) -> Sequence[OptionQuote]:
        self.log.warning(
            "option_chain_unavailable",
            extra={"event": "option_chain_unavailable", "symbol": symbol.upper()},
        )
        return []

    async def _stream_credentials(self) -> tuple[str, str]:
        response = await self.auth.request("GET", "/api-quote-tokens")
        data = response.json()["data"]
        return data["dxlink-url"], data["token"]

    async def get_market_session(self) -> dict[str, Any]:
        response = await self.auth.request(
            "GET",
            "/market-time/sessions/current",
            params={"instrument-collections[]": "Equity"},
        )
        data = response.json().get("data")
        if not isinstance(data, dict):
            raise MarketDataError("market-session response did not contain an object")
        items = data.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
        return data

    async def _send_setup(
        self, socket: Any, token: str, event_fields: dict[str, list[str]]
    ) -> None:
        await socket.send(
            json.dumps(
                {
                    "type": "SETUP",
                    "channel": 0,
                    "version": "0.1-DXF-JS/0.3.0",
                    "keepaliveTimeout": 60,
                    "acceptKeepaliveTimeout": 60,
                }
            )
        )
        await self._receive_until(
            socket,
            lambda message: message.get("type") == "SETUP",
            "SETUP acknowledgement",
        )
        auth_state = await self._receive_until(
            socket,
            lambda message: message.get("type") == "AUTH_STATE",
            "AUTH_STATE",
        )
        if auth_state.get("state") != "AUTHORIZED":
            if auth_state.get("state") != "UNAUTHORIZED":
                raise MarketDataError(
                    f"unexpected DXLink auth state: {auth_state.get('state', 'missing')}"
                )
            await socket.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
            await self._receive_until(
                socket,
                lambda message: (
                    message.get("type") == "AUTH_STATE" and message.get("state") == "AUTHORIZED"
                ),
                "AUTHORIZED state",
            )
        await socket.send(
            json.dumps(
                {
                    "type": "CHANNEL_REQUEST",
                    "channel": 3,
                    "service": "FEED",
                    "parameters": {"contract": "AUTO"},
                }
            )
        )
        await self._receive_until(
            socket,
            lambda message: message.get("type") == "CHANNEL_OPENED" and message.get("channel") == 3,
            "CHANNEL_OPENED",
        )
        await socket.send(
            json.dumps(
                {
                    "type": "FEED_SETUP",
                    "channel": 3,
                    "acceptAggregationPeriod": 0.1,
                    "acceptDataFormat": "COMPACT",
                    "acceptEventFields": event_fields,
                }
            )
        )
        await self._receive_until(
            socket,
            lambda message: message.get("type") == "FEED_CONFIG" and message.get("channel") == 3,
            "FEED_CONFIG",
        )

    async def _receive_until(
        self,
        socket: Any,
        predicate: Any,
        description: str,
        timeout_seconds: float = 10,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                payload = await asyncio.wait_for(socket.recv(), timeout=remaining)
            except TimeoutError as exc:
                raise MarketDataError(f"timed out waiting for DXLink {description}") from exc
            message = json.loads(payload)
            if message.get("type") == "ERROR":
                raise MarketDataError(f"DXLink error while waiting for {description}")
            if predicate(message):
                return message
        raise MarketDataError(f"timed out waiting for DXLink {description}")

    async def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]:
        wanted = sorted({symbol.upper() for symbol in symbols})
        backoff = 1.0
        while not self._closed:
            try:
                self.connection_state = ConnectionState.CONNECTING
                self.log.info(
                    "market_data_connecting",
                    extra={
                        "event": "market_data_connecting",
                        "connection_state": self.connection_state.value,
                    },
                )
                url, token = await self._stream_credentials()
                async with websockets.connect(url, ping_interval=None, close_timeout=5) as socket:
                    await self._send_setup(
                        socket,
                        token,
                        {
                            "Quote": [
                                "eventType",
                                "eventSymbol",
                                "bidPrice",
                                "askPrice",
                                "bidSize",
                                "askSize",
                            ],
                            "Trade": [
                                "eventType",
                                "eventSymbol",
                                "price",
                                "dayVolume",
                                "size",
                            ],
                        },
                    )
                    add = [
                        {"type": event_type, "symbol": symbol}
                        for symbol in wanted
                        for event_type in ("Quote", "Trade")
                    ]
                    await socket.send(
                        json.dumps(
                            {"type": "FEED_SUBSCRIPTION", "channel": 3, "reset": True, "add": add}
                        )
                    )
                    self.connection_state = ConnectionState.CONNECTED
                    self.log.info(
                        "market_data_connected",
                        extra={
                            "event": "market_data_connected",
                            "connection_state": self.connection_state.value,
                        },
                    )
                    backoff = 1.0
                    keepalive = asyncio.create_task(self._keepalive(socket))
                    try:
                        async for message in socket:
                            for event in self._parse_message(message):
                                if isinstance(event, QuoteEvent):
                                    self._quotes[event.symbol] = event
                                yield event
                    finally:
                        keepalive.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connection_state = ConnectionState.DISCONNECTED
                self.log.exception(
                    "market_data_disconnected",
                    extra={
                        "event": "market_data_disconnected",
                        "connection_state": self.connection_state.value,
                    },
                )
                if self._closed:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _keepalive(self, socket: object) -> None:
        while not self._closed:
            await asyncio.sleep(30)
            await socket.send(json.dumps({"type": "KEEPALIVE", "channel": 0}))  # type: ignore[attr-defined]

    def _parse_message(self, message: str | bytes) -> list[MarketEvent]:
        raw = json.loads(message)
        if raw.get("type") != "FEED_DATA":
            return []
        data = raw.get("data", [])
        event_type = data[0] if data else None
        now = datetime.now(UTC)
        events: list[MarketEvent] = []
        width = 6 if event_type == "Quote" else 5 if event_type == "Trade" else 0
        for row in _compact_rows(data, width):
            if event_type == "Quote":
                events.append(
                    QuoteEvent(
                        symbol=row[1],
                        timestamp=now,
                        bid=_float_or_none(row[2]),
                        ask=_float_or_none(row[3]) if len(row) > 3 else None,
                    )
                )
            elif event_type == "Trade":
                price = _float_or_none(row[2])
                if price is not None:
                    events.append(
                        TradeEvent(
                            symbol=row[1],
                            timestamp=now,
                            price=price,
                            size=_float_or_none(row[4]) or 0,
                        )
                    )
        return events

    def _parse_candle_message(self, message: str | bytes) -> list[CandleEvent]:
        raw = json.loads(message)
        if raw.get("type") != "FEED_DATA":
            return []
        data = raw.get("data", [])
        if not data or data[0] != "Candle":
            return []
        candles: list[CandleEvent] = []
        for row in _compact_rows(data, 12):
            timestamp_ms = _finite_float(row[4])
            open_price = _finite_float(row[7])
            high = _finite_float(row[8])
            low = _finite_float(row[9])
            close = _finite_float(row[10])
            volume = _finite_float(row[11])
            if None in {timestamp_ms, open_price, high, low, close}:
                continue
            assert timestamp_ms is not None
            assert open_price is not None
            assert high is not None
            assert low is not None
            assert close is not None
            try:
                symbol = str(row[1]).split("{", 1)[0].upper()
                candles.append(
                    CandleEvent(
                        symbol=symbol,
                        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
                        interval_seconds=60,
                        open=open_price,
                        high=high,
                        low=low,
                        close=close,
                        volume=max(volume or 0, 0),
                    )
                )
            except ValueError:
                self.log.warning(
                    "invalid_candle_discarded",
                    extra={"event": "invalid_candle_discarded", "symbol": str(row[1])},
                )
        return candles

    @staticmethod
    def _candle_snapshot_completions(message: str | bytes) -> set[str]:
        raw = json.loads(message)
        data = raw.get("data", []) if raw.get("type") == "FEED_DATA" else []
        if not data or data[0] != "Candle":
            return set()
        completed: set[str] = set()
        for row in _compact_rows(data, 12):
            flags = int(_finite_float(row[2]) or 0)
            if flags & 0x18:  # SNAPSHOT_END (0x08) or SNAPSHOT_SNIP (0x10)
                completed.add(str(row[1]).split("{", 1)[0].upper())
        return completed

    async def close(self) -> None:
        self._closed = True
        self.connection_state = ConnectionState.DISCONNECTED


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _finite_float(value: Any) -> float | None:
    number = _float_or_none(value)
    return number if number is not None and math.isfinite(number) else None


def _compact_rows(data: Any, width: int) -> list[list[Any]]:
    """Expand DXLink COMPACT batches into fixed-width event rows."""
    if width <= 0 or not isinstance(data, list):
        return []
    rows: list[list[Any]] = []
    for values in data[1:]:
        if not isinstance(values, list):
            continue
        rows.extend(
            values[offset : offset + width]
            for offset in range(0, len(values), width)
            if len(values[offset : offset + width]) == width
        )
    return rows
