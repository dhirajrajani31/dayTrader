import json
from datetime import UTC, datetime

from app.market_data.models import CandleEvent
from app.market_data.tastytrade import TastytradeMarketDataProvider


def test_compact_candle_message_is_normalized():
    provider = TastytradeMarketDataProvider("temporary-token")
    timestamp = datetime(2026, 9, 29, 14, 30, tzinfo=UTC)
    message = json.dumps(
        {
            "type": "FEED_DATA",
            "channel": 3,
            "data": [
                "Candle",
                [
                    "Candle",
                    "SPY{=1m}",
                    0,
                    1,
                    int(timestamp.timestamp() * 1000),
                    0,
                    10,
                    500.0,
                    501.0,
                    499.5,
                    500.5,
                    12345,
                ],
            ],
        }
    )
    candles = provider._parse_candle_message(message)
    assert candles == [
        CandleEvent(
            symbol="SPY",
            timestamp=timestamp,
            interval_seconds=60,
            open=500,
            high=501,
            low=499.5,
            close=500.5,
            volume=12345,
        )
    ]


def test_nan_or_invalid_candles_are_discarded():
    provider = TastytradeMarketDataProvider("temporary-token")
    message = json.dumps(
        {
            "type": "FEED_DATA",
            "data": [
                "Candle",
                ["Candle", "SPY{=1m}", 0, 1, 1_700_000_000_000, 0, 1, "NaN", 2, 1, 2, 10],
                ["Candle", "SPY{=1m}", 0, 1, 1_700_000_060_000, 0, 1, 2, 1, 0, 2, 10],
            ],
        }
    )
    assert provider._parse_candle_message(message) == []


def test_snapshot_end_and_snip_flags_are_detected():
    provider = TastytradeMarketDataProvider("temporary-token")
    message = json.dumps(
        {
            "type": "FEED_DATA",
            "data": [
                "Candle",
                ["Candle", "SPY{=1m}", 0x08] + [0] * 9,
                ["Candle", "QQQ{=1m}", 0x10] + [0] * 9,
                ["Candle", "AAPL{=1m}", 0x04] + [0] * 9,
            ],
        }
    )
    assert provider._candle_snapshot_completions(message) == {"SPY", "QQQ"}


def test_concatenated_compact_candle_batch_decodes_every_row():
    provider = TastytradeMarketDataProvider("temporary-token")
    first = [
        "Candle",
        "SPY{=1m}",
        0x04,
        1,
        1_700_000_000_000,
        0,
        1,
        100,
        101,
        99,
        100.5,
        10,
    ]
    second = [
        "Candle",
        "SPY{=1m}",
        0x08,
        2,
        1_700_000_060_000,
        0,
        1,
        100.5,
        102,
        100,
        101.5,
        20,
    ]
    message = json.dumps({"type": "FEED_DATA", "data": ["Candle", first + second]})
    candles = provider._parse_candle_message(message)
    assert len(candles) == 2
    assert candles[0].open == 100
    assert candles[1].close == 101.5
    assert provider._candle_snapshot_completions(message) == {"SPY"}
