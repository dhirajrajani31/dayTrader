from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.market_data.models import CandleEvent, QuoteEvent
from app.strategy.features import aggregate_candles, build_feature_snapshot, vwap
from app.strategy.models import DataQuality


def candles(count=6):
    start = datetime(2026, 1, 5, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    return [
        CandleEvent(
            symbol="TEST",
            timestamp=start + timedelta(minutes=i),
            interval_seconds=60,
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=100,
        )
        for i in range(count)
    ]


def test_vwap_and_five_minute_aggregation():
    values = candles()
    assert vwap(values) == pytest.approx(
        sum((c.high + c.low + c.close) / 3 for c in values) / len(values)
    )
    aggregate = aggregate_candles(values, 300)
    assert len(aggregate) == 2
    assert aggregate[0].open == 100
    assert aggregate[0].high == 105
    assert aggregate[0].volume == 500


def test_unavailable_features_are_partial_not_guessed():
    values = candles()
    now = values[-1].timestamp
    snapshot = build_feature_snapshot("TEST", now, values)
    assert snapshot.premarket_high.value is None
    assert snapshot.premarket_high.quality == DataQuality.PARTIAL
    assert snapshot.relative_volume.quality == DataQuality.PARTIAL


def test_stale_quote_marks_calculated_features_stale():
    values = candles()
    quote = QuoteEvent(symbol="TEST", timestamp=values[-1].timestamp, bid=104, ask=105, last=104.5)
    snapshot = build_feature_snapshot(
        "TEST", quote.timestamp + timedelta(seconds=31), values, quote
    )
    assert snapshot.price.quality == DataQuality.STALE
    assert snapshot.vwap.quality == DataQuality.STALE


def test_naive_market_timestamp_rejected():
    with pytest.raises(ValueError):
        QuoteEvent(symbol="TEST", timestamp=datetime(2026, 1, 1), last=100)


def test_historical_warmup_populates_reference_features_and_rvol():
    chicago = ZoneInfo("America/Chicago")
    previous_start = datetime(2026, 1, 5, 8, 30, tzinfo=chicago)
    current_start = datetime(2026, 1, 6, 8, 30, tzinfo=chicago)
    previous = [
        CandleEvent(
            symbol="TEST",
            timestamp=previous_start + timedelta(minutes=index),
            interval_seconds=60,
            open=100,
            high=101 + index / 10,
            low=99 - index / 10,
            close=100 + index / 10,
            volume=100,
        )
        for index in range(15)
    ]
    premarket = [
        CandleEvent(
            symbol="TEST",
            timestamp=datetime(2026, 1, 6, 8, 0, tzinfo=chicago),
            interval_seconds=60,
            open=102,
            high=103,
            low=101,
            close=102,
            volume=10,
        )
    ]
    current = [
        CandleEvent(
            symbol="TEST",
            timestamp=current_start + timedelta(minutes=index),
            interval_seconds=60,
            open=102,
            high=103 + index / 10,
            low=101 - index / 10,
            close=102 + index / 10,
            volume=200,
        )
        for index in range(15)
    ]
    snapshot = build_feature_snapshot("TEST", current[-1].timestamp, previous + premarket + current)
    assert snapshot.previous_day_high.value == previous[-1].high
    assert snapshot.previous_day_low.value == previous[-1].low
    assert snapshot.previous_close.value == previous[-1].close
    assert snapshot.premarket_high.value == 103
    assert snapshot.opening_range_5_high.value == current[4].high
    assert snapshot.opening_range_15_low.value == current[-1].low
    assert snapshot.relative_volume.value == pytest.approx(2.0)
