import json
from datetime import UTC, datetime

from app.market_data.models import CandleEvent, OptionQuote
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


def test_nested_option_chain_uses_nearest_eligible_standard_expiration():
    provider = TastytradeMarketDataProvider("temporary-token")
    chains = [
        {
            "option-chain-type": "Standard",
            "shares-per-contract": 100,
            "expirations": [
                {
                    "expiration-date": "2026-09-30",
                    "days-to-expiration": 0,
                    "strikes": [],
                },
                {
                    "expiration-date": "2026-10-02",
                    "days-to-expiration": 2,
                    "strikes": [
                        {
                            "strike-price": "100.0",
                            "call": "TEST  261002C00100000",
                            "call-streamer-symbol": ".TEST261002C100",
                            "put": "TEST  261002P00100000",
                            "put-streamer-symbol": ".TEST261002P100",
                        }
                    ],
                },
            ],
        }
    ]

    contracts = provider._option_contracts(chains, "TEST", 100)

    assert {item.call_put for item in contracts} == {"CALL", "PUT"}
    assert {item.days_to_expiration for item in contracts} == {2}
    assert all(item.shares_per_contract == 100 for item in contracts)


def test_option_dxlink_quote_greeks_summary_and_volume_are_merged():
    provider = TastytradeMarketDataProvider("temporary-token")
    option = OptionQuote(
        symbol="TEST  261002P00100000",
        streamer_symbol=".TEST261002P100",
        underlying_symbol="TEST",
        expiration=datetime(2026, 10, 2, 20, tzinfo=UTC),
        days_to_expiration=2,
        strike=100,
        call_put="PUT",
    )
    values = {option.streamer_symbol: option.model_dump()}
    messages = [
        ["Quote", ["Quote", option.streamer_symbol, 1.0, 1.1, 10, 12]],
        ["Trade", ["Trade", option.streamer_symbol, 1.05, 250, 1]],
        ["Greeks", ["Greeks", option.streamer_symbol, 0.55, -0.62, 0.04, -0.08, 0.01, 0.12]],
        ["Summary", ["Summary", option.streamer_symbol, 500, 1.2, 1.3, 0.9, 1.0]],
    ]
    for data in messages:
        assert provider._merge_option_message(
            json.dumps({"type": "FEED_DATA", "data": data}), values
        )

    enriched = OptionQuote.model_validate(values[option.streamer_symbol])
    assert enriched.bid == 1.0
    assert enriched.ask == 1.1
    assert enriched.delta == -0.62
    assert enriched.theta == -0.08
    assert enriched.iv == 0.55
    assert enriched.open_interest == 500
    assert enriched.volume == 250
