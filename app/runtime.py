from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import StrategySettings
from app.market_data.base import MarketDataProvider
from app.market_data.models import CandleEvent, QuoteEvent, TradeEvent
from app.strategy.engine import StrategyEngine
from app.strategy.features import build_feature_snapshot, momentum, relative_strength, vwap
from app.strategy.levels import nearest_levels, reference_levels, swing_levels
from app.strategy.models import Direction, Level, SetupState, StrategyObservation
from app.strategy.rules import candidate_assessment


class CandleBuilder:
    """Builds one-minute candles from normalized trades without retaining every tick."""

    def __init__(self) -> None:
        self.current: dict[str, CandleEvent] = {}

    def add(self, trade: TradeEvent) -> CandleEvent | None:
        minute = trade.timestamp.replace(second=0, microsecond=0)
        active = self.current.get(trade.symbol)
        completed = None
        if active is None or active.timestamp != minute:
            completed = active
            self.current[trade.symbol] = CandleEvent(
                symbol=trade.symbol,
                timestamp=minute,
                interval_seconds=60,
                open=trade.price,
                high=trade.price,
                low=trade.price,
                close=trade.price,
                volume=trade.size,
            )
        else:
            self.current[trade.symbol] = active.model_copy(
                update={
                    "high": max(active.high, trade.price),
                    "low": min(active.low, trade.price),
                    "close": trade.price,
                    "volume": active.volume + trade.size,
                }
            )
        return completed


class MarketMonitor:
    """Resilient normalized-feed monitor and feature calculator.

    Strategy evaluation is intentionally withheld while historical baselines/levels are partial;
    this prevents fabricated confirmations in live mode.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        stale_after_seconds: int = 30,
        strategy: LiveStrategyCoordinator | None = None,
        on_completed_candle: Callable[[CandleEvent], Awaitable[None]] | None = None,
    ):
        self.provider = provider
        self.stale_after_seconds = stale_after_seconds
        self.builder = CandleBuilder()
        self.candles: dict[str, list[CandleEvent]] = {}
        self.quotes: dict[str, QuoteEvent] = {}
        self.last_quote_timestamp: dict[str, datetime] = {}
        self.log = logging.getLogger("tradingpilot.runtime")
        self.strategy = strategy
        self.on_completed_candle = on_completed_candle

    def seed_candles(self, history: dict[str, list[CandleEvent]]) -> None:
        for symbol, candles in history.items():
            indexed = {candle.timestamp: candle for candle in candles}
            self.candles[symbol.upper()] = sorted(
                indexed.values(), key=lambda candle: candle.timestamp
            )[-10_000:]

    async def run(
        self, symbols: Sequence[str], on_status: Callable[[dict], Awaitable[None]] | None = None
    ) -> None:
        async for event in self.provider.subscribe(symbols):
            if isinstance(event, QuoteEvent):
                self.quotes[event.symbol] = event
                self.last_quote_timestamp[event.symbol] = event.timestamp
            elif isinstance(event, TradeEvent):
                complete = self.builder.add(event)
                if complete:
                    values = self.candles.setdefault(event.symbol, [])
                    indexed = {candle.timestamp: candle for candle in values}
                    indexed[complete.timestamp] = complete
                    values[:] = sorted(indexed.values(), key=lambda candle: candle.timestamp)[
                        -10_000:
                    ]
                    snapshot = build_feature_snapshot(
                        event.symbol,
                        event.timestamp,
                        values,
                        self.quotes.get(event.symbol),
                        stale_after_seconds=self.stale_after_seconds,
                    )
                    self.log.debug(
                        "feature_snapshot",
                        extra={"event": "feature_snapshot", "symbol": event.symbol},
                    )
                    if on_status:
                        await on_status(snapshot.model_dump(mode="json"))
                    if self.strategy:
                        await self.strategy.evaluate(event.symbol, values)
                    if self.on_completed_candle:
                        await self.on_completed_candle(complete)


class LiveStrategyCoordinator:
    """Turns completed normalized candles into conservative strategy observations."""

    def __init__(self, engine: StrategyEngine, settings: StrategySettings):
        self.engine = engine
        self.settings = settings
        self.history: dict[str, list[CandleEvent]] = {}
        self.log = logging.getLogger("tradingpilot.strategy.candidates")
        self._candidate_log_state: dict[
            tuple[str, Direction], tuple[datetime, tuple[object, ...]]
        ] = {}

    async def evaluate(self, symbol: str, candles: Sequence[CandleEvent]) -> None:
        self.history[symbol] = list(candles)
        if symbol in {"SPY", "QQQ"} or len(candles) < 6:
            return
        last = candles[-1]
        chicago = ZoneInfo("America/Chicago")
        session_date = last.timestamp.astimezone(chicago).date()
        today = [
            candle
            for candle in candles
            if candle.timestamp.astimezone(chicago).date() == session_date
        ]
        regular = [
            candle
            for candle in today
            if (8, 30)
            <= (
                candle.timestamp.astimezone(chicago).hour,
                candle.timestamp.astimezone(chicago).minute,
            )
            < (15, 0)
        ]
        premarket = [
            candle
            for candle in today
            if (3, 0)
            <= (
                candle.timestamp.astimezone(chicago).hour,
                candle.timestamp.astimezone(chicago).minute,
            )
            < (8, 30)
        ]
        previous_regular: list[CandleEvent] = []
        previous_regular_dates = sorted(
            {
                candle.timestamp.astimezone(chicago).date()
                for candle in candles
                if candle.timestamp.astimezone(chicago).date() < session_date
                and (8, 30)
                <= (
                    candle.timestamp.astimezone(chicago).hour,
                    candle.timestamp.astimezone(chicago).minute,
                )
                < (15, 0)
            }
        )
        if previous_regular_dates:
            previous_date = previous_regular_dates[-1]
            previous_regular = [
                candle
                for candle in candles
                if candle.timestamp.astimezone(chicago).date() == previous_date
                and (8, 30)
                <= (
                    candle.timestamp.astimezone(chicago).hour,
                    candle.timestamp.astimezone(chicago).minute,
                )
                < (15, 0)
            ]
        values: dict[str, float | None] = {}
        if previous_regular:
            values["PREVIOUS_DAY_HIGH"] = max(c.high for c in previous_regular)
            values["PREVIOUS_DAY_LOW"] = min(c.low for c in previous_regular)
            values["PREVIOUS_CLOSE"] = previous_regular[-1].close
        if premarket:
            values["PREMARKET_HIGH"] = max(c.high for c in premarket)
            values["PREMARKET_LOW"] = min(c.low for c in premarket)
        if regular:
            values["SESSION_HIGH"] = max(c.high for c in regular)
            values["SESSION_LOW"] = min(c.low for c in regular)
        if len(regular) >= 5:
            first_five = regular[:5]
            values["OPENING_RANGE_5_HIGH"] = max(c.high for c in first_five)
            values["OPENING_RANGE_5_LOW"] = min(c.low for c in first_five)
        if len(regular) >= 15:
            values["OPENING_RANGE_15_HIGH"] = max(c.high for c in regular[:15])
            values["OPENING_RANGE_15_LOW"] = min(c.low for c in regular[:15])
        active_session = regular or today
        levels = reference_levels(values, last.timestamp) + swing_levels(active_session[-40:])
        if not levels:
            return
        support, resistance = nearest_levels(last.close, levels)
        last_local = last.timestamp.astimezone(chicago)
        same_minute_history = [
            candle.volume
            for candle in candles
            if candle.timestamp.astimezone(chicago).date() < session_date
            and (
                candle.timestamp.astimezone(chicago).hour,
                candle.timestamp.astimezone(chicago).minute,
            )
            == (last_local.hour, last_local.minute)
        ]
        rolling_history = active_session[-21:-1]
        baseline = same_minute_history or [candle.volume for candle in rolling_history]
        average_volume = sum(baseline) / len(baseline) if baseline else 0
        rvol = last.volume / average_volume if average_volume > 0 else None
        calculated_vwap = vwap(active_session)
        stock_return = (
            last.close / active_session[0].open - 1
            if active_session and active_session[0].open
            else None
        )
        benchmark = self.history.get("QQQ") or self.history.get("SPY")
        benchmark_today = [
            candle
            for candle in (benchmark or [])
            if candle.timestamp.astimezone(chicago).date() == session_date
        ]
        benchmark_return = (
            benchmark_today[-1].close / benchmark_today[0].open - 1
            if benchmark_today and benchmark_today[0].open
            else None
        )
        rs = relative_strength(stock_return, benchmark_return)
        benchmark_momentum = momentum(benchmark_today) if benchmark_today else None
        for direction, level in (
            (Direction.BULLISH, resistance),
            (Direction.BEARISH, support),
        ):
            if level is None:
                continue
            next_level = self._next_level(level.midpoint, direction, levels)
            reward_risk = self._reward_risk(last.close, level.midpoint, next_level)
            observation = StrategyObservation(
                symbol=symbol,
                timestamp=last.timestamp,
                direction=direction,
                price=last.close,
                candle_open=last.open,
                candle_high=last.high,
                candle_low=last.low,
                level=level,
                vwap=calculated_vwap,
                relative_volume=rvol,
                relative_strength=rs,
                momentum=momentum(active_session),
                volume_confirmed=(
                    rvol is not None and rvol >= self.settings.minimum_relative_volume
                ),
                market_agrees=(
                    benchmark_momentum is not None
                    and (
                        (direction == Direction.BULLISH and benchmark_momentum > 0)
                        or (direction == Direction.BEARISH and benchmark_momentum < 0)
                    )
                ),
                opening_range_context=level.type.startswith("OPENING_RANGE"),
                reward_risk=reward_risk,
                next_level=next_level,
            )
            self._log_interesting_candidate(observation)
            await self.engine.evaluate(observation)

    def _log_interesting_candidate(self, observation: StrategyObservation) -> None:
        machine = self.engine.machines.get((observation.symbol, observation.direction))
        if machine is not None and machine.state != SetupState.WATCHING:
            return
        key = (observation.symbol, observation.direction)
        assessment = candidate_assessment(observation, self.settings)
        if not assessment.interesting or observation.level is None:
            self._candidate_log_state.pop(key, None)
            return
        signature: tuple[object, ...] = (
            observation.level.type,
            round(observation.level.midpoint, 4),
            assessment.passed,
        )
        previous = self._candidate_log_state.get(key)
        cooldown = timedelta(seconds=self.settings.candidate_log_cooldown_seconds)
        if previous:
            previous_time, previous_signature = previous
            if signature == previous_signature and observation.timestamp - previous_time < cooldown:
                return
        distance_pct = abs(observation.price - observation.level.midpoint) / (
            observation.level.midpoint
        )
        self.log.info(
            "interesting_candidate",
            extra={
                "event": "interesting_candidate",
                "symbol": observation.symbol,
                "direction": observation.direction.value,
                "price": round(observation.price, 4),
                "level_type": observation.level.type,
                "level_price": round(observation.level.midpoint, 4),
                "distance_pct": round(distance_pct, 6),
                "relative_volume": observation.relative_volume,
                "relative_strength": observation.relative_strength,
                "reward_risk": observation.reward_risk,
                "check_score": assessment.score,
                "check_total": assessment.total,
                "passed_checks": assessment.passed,
                "missing_checks": assessment.missing,
            },
        )
        self._candidate_log_state[key] = (observation.timestamp, signature)

    @staticmethod
    def _next_level(current: float, direction: Direction, levels: Sequence[Level]) -> float | None:
        if direction == Direction.BULLISH:
            candidates = sorted(level.midpoint for level in levels if level.midpoint > current)
        else:
            candidates = sorted(
                (level.midpoint for level in levels if level.midpoint < current), reverse=True
            )
        return candidates[0] if candidates else None

    def _reward_risk(self, price: float, level: float, target: float | None) -> float | None:
        if target is None:
            return None
        risk = abs(price - level) + level * self.settings.invalidation_buffer_pct
        return abs(target - price) / risk if risk > 0 else None
