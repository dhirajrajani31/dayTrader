from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.market_data.models import CandleEvent
from app.strategy.levels import make_zone, nearest_levels, reference_levels, swing_levels


def test_levels_are_zones_and_nearest_are_selected():
    now = datetime.now(ZoneInfo("America/Chicago"))
    levels = reference_levels(
        {"PREVIOUS_DAY_LOW": 98, "PREVIOUS_CLOSE": 100, "PREVIOUS_DAY_HIGH": 102}, now
    )
    assert all(level.zone.low < level.zone.high for level in levels)
    support, resistance = nearest_levels(101, levels)
    assert support.type == "PREVIOUS_CLOSE"
    assert resistance.type == "PREVIOUS_DAY_HIGH"


def test_swing_high_and_low_detection():
    start = datetime(2026, 1, 5, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    highs = [10, 11, 15, 11, 10]
    lows = [8, 7, 5, 7, 8]
    values = [
        CandleEvent(
            symbol="X",
            timestamp=start + timedelta(minutes=i),
            interval_seconds=60,
            open=9,
            high=highs[i],
            low=lows[i],
            close=9,
            volume=10,
        )
        for i in range(5)
    ]
    levels = swing_levels(values, window=2)
    assert {level.type for level in levels} == {"SWING_HIGH", "SWING_LOW"}
    assert make_zone(100).contains(100)
