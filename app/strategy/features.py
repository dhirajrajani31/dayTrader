from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.market_data.models import CandleEvent, QuoteEvent
from app.strategy.models import DataQuality, FeatureSnapshot, FeatureValue


def _feature(
    value: float | None, as_of: datetime, stale: bool = False, reason: str | None = None
) -> FeatureValue:
    quality = (
        DataQuality.STALE
        if stale
        else (DataQuality.READY if value is not None else DataQuality.PARTIAL)
    )
    return FeatureValue(value=value, quality=quality, as_of=as_of, reason=reason)


def vwap(candles: Sequence[CandleEvent]) -> float | None:
    volume = sum(c.volume for c in candles)
    if not candles or volume <= 0:
        return None
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles) / volume


def momentum(candles: Sequence[CandleEvent], periods: int = 5) -> float | None:
    if len(candles) < periods + 1 or candles[-periods - 1].close == 0:
        return None
    return candles[-1].close / candles[-periods - 1].close - 1


def aggregate_candles(candles: Sequence[CandleEvent], interval_seconds: int) -> list[CandleEvent]:
    """Aggregate ordered normalized candles into a larger fixed interval."""
    if not candles:
        return []
    groups: dict[int, list[CandleEvent]] = {}
    for candle in candles:
        epoch = int(candle.timestamp.timestamp())
        bucket = epoch - epoch % interval_seconds
        groups.setdefault(bucket, []).append(candle)
    result: list[CandleEvent] = []
    for bucket in sorted(groups):
        group = groups[bucket]
        result.append(
            CandleEvent(
                symbol=group[0].symbol,
                timestamp=datetime.fromtimestamp(bucket, tz=group[0].timestamp.tzinfo),
                interval_seconds=interval_seconds,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return result


def build_feature_snapshot(
    symbol: str,
    now: datetime,
    candles: Sequence[CandleEvent],
    quote: QuoteEvent | None = None,
    average_volume: float | None = None,
    stale_after_seconds: int = 30,
) -> FeatureSnapshot:
    latest = candles[-1] if candles else None
    price = quote.last if quote and quote.last is not None else (latest.close if latest else None)
    event_time = quote.timestamp if quote else (latest.timestamp if latest else now)
    stale = now - event_time > timedelta(seconds=stale_after_seconds)
    chicago = ZoneInfo("America/Chicago")
    session_date = (latest.timestamp if latest else now).astimezone(chicago).date()
    today = [
        candle for candle in candles if candle.timestamp.astimezone(chicago).date() == session_date
    ]
    regular = [candle for candle in today if _in_regular_market(candle, chicago)]
    premarket = [candle for candle in today if _in_premarket(candle, chicago)]
    active_session = regular or today
    previous_dates = sorted(
        {
            candle.timestamp.astimezone(chicago).date()
            for candle in candles
            if candle.timestamp.astimezone(chicago).date() < session_date
            and _in_regular_market(candle, chicago)
        }
    )
    previous_regular = (
        [
            candle
            for candle in candles
            if candle.timestamp.astimezone(chicago).date() == previous_dates[-1]
            and _in_regular_market(candle, chicago)
        ]
        if previous_dates
        else []
    )
    calculated_vwap = vwap(active_session)
    volume = sum(c.volume for c in active_session[-5:]) if active_session else None
    if average_volume is None and latest:
        current_time = latest.timestamp.astimezone(chicago)
        historical_windows: list[float] = []
        for historical_date in previous_dates:
            window = [
                candle
                for candle in candles
                if candle.timestamp.astimezone(chicago).date() == historical_date
                and 0
                <= (
                    current_time.hour * 60
                    + current_time.minute
                    - (
                        candle.timestamp.astimezone(chicago).hour * 60
                        + candle.timestamp.astimezone(chicago).minute
                    )
                )
                < 5
            ]
            if window:
                historical_windows.append(sum(candle.volume for candle in window))
        average_volume = (
            sum(historical_windows) / len(historical_windows) if historical_windows else None
        )
    rvol = (
        volume / average_volume
        if volume is not None and average_volume and average_volume > 0
        else None
    )
    distance = (price / calculated_vwap - 1) if price is not None and calculated_vwap else None
    return FeatureSnapshot(
        symbol=symbol,
        timestamp=now,
        price=_feature(price, event_time, stale),
        bid=_feature(quote.bid if quote else None, event_time, stale),
        ask=_feature(quote.ask if quote else None, event_time, stale),
        spread=_feature(quote.spread if quote else None, event_time, stale),
        vwap=_feature(calculated_vwap, event_time, stale),
        distance_from_vwap=_feature(distance, event_time, stale),
        session_high=_feature(
            max((c.high for c in active_session), default=None), event_time, stale
        ),
        session_low=_feature(min((c.low for c in active_session), default=None), event_time, stale),
        premarket_high=_feature(max((c.high for c in premarket), default=None), event_time, stale),
        premarket_low=_feature(min((c.low for c in premarket), default=None), event_time, stale),
        previous_day_high=_feature(
            max((c.high for c in previous_regular), default=None), event_time, stale
        ),
        previous_day_low=_feature(
            min((c.low for c in previous_regular), default=None), event_time, stale
        ),
        previous_close=_feature(
            previous_regular[-1].close if previous_regular else None, event_time, stale
        ),
        opening_range_5_high=_feature(
            max((c.high for c in regular[:5]), default=None) if len(regular) >= 5 else None,
            event_time,
            stale,
        ),
        opening_range_5_low=_feature(
            min((c.low for c in regular[:5]), default=None) if len(regular) >= 5 else None,
            event_time,
            stale,
        ),
        opening_range_15_high=_feature(
            max((c.high for c in regular[:15]), default=None) if len(regular) >= 15 else None,
            event_time,
            stale,
        ),
        opening_range_15_low=_feature(
            min((c.low for c in regular[:15]), default=None) if len(regular) >= 15 else None,
            event_time,
            stale,
        ),
        rolling_volume=_feature(volume, event_time, stale),
        relative_volume=_feature(
            rvol, event_time, stale, "needs historical baseline" if rvol is None else None
        ),
        momentum=_feature(momentum(active_session), event_time, stale),
    )


def relative_strength(stock_return: float | None, benchmark_return: float | None) -> float | None:
    if stock_return is None or benchmark_return is None:
        return None
    return stock_return - benchmark_return


def _in_regular_market(candle: CandleEvent, timezone: ZoneInfo) -> bool:
    local = candle.timestamp.astimezone(timezone)
    return (8, 30) <= (local.hour, local.minute) < (15, 0)


def _in_premarket(candle: CandleEvent, timezone: ZoneInfo) -> bool:
    local = candle.timestamp.astimezone(timezone)
    return (3, 0) <= (local.hour, local.minute) < (8, 30)
