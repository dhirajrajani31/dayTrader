from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.strategy.levels import make_zone
from app.strategy.models import Direction, Level, StrategyObservation


def scenario(
    outcome: str = "triggered", direction: Direction = Direction.BULLISH, symbol: str = "DEMO"
) -> list[StrategyObservation]:
    """Build deterministic approach/break/retest sequences for demo and tests."""
    start = datetime(2026, 1, 5, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    level = Level(
        type="OPENING_RANGE",
        zone=make_zone(100, 0.001),
        source="synthetic",
        confirmations=2,
        timestamp=start,
    )
    bullish = direction == Direction.BULLISH

    def obs(
        minute: int, price: float, open_: float, high: float, low: float, volume: bool = True
    ) -> StrategyObservation:
        return StrategyObservation(
            symbol=symbol,
            timestamp=start + timedelta(minutes=minute),
            direction=direction,
            price=price,
            candle_open=open_,
            candle_high=high,
            candle_low=low,
            level=level,
            vwap=99.5 if bullish else 100.5,
            relative_volume=2.1,
            relative_strength=0.015 if bullish else -0.015,
            momentum=0.01 if bullish else -0.01,
            volume_confirmed=volume,
            market_agrees=True,
            opening_range_context=True,
            reward_risk=2.0,
            next_level=103 if bullish else 97,
        )

    if bullish:
        frames = [obs(0, 99.9, 99.8, 100.0, 99.7), obs(1, 100.3, 99.9, 100.4, 99.9)]
        if outcome == "triggered":
            frames.append(obs(2, 100.25, 100.08, 100.3, 100.02))
        elif outcome == "invalidated":
            frames.append(obs(2, 99.6, 100.1, 100.15, 99.5))
        elif outcome == "extended":
            frames.append(obs(2, 101.5, 100.5, 101.6, 100.45))
    else:
        frames = [obs(0, 100.1, 100.2, 100.3, 100.0), obs(1, 99.7, 100.1, 100.15, 99.6)]
        if outcome == "triggered":
            frames.append(obs(2, 99.75, 99.92, 99.98, 99.65))
        elif outcome == "invalidated":
            frames.append(obs(2, 100.4, 99.9, 100.5, 99.85))
        elif outcome == "extended":
            frames.append(obs(2, 98.5, 99.5, 99.55, 98.4))
    return frames
