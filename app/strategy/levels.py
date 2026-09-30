from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from app.market_data.models import CandleEvent
from app.strategy.models import Level, PriceZone


def make_zone(price: float, width_pct: float = 0.001) -> PriceZone:
    half = abs(price) * width_pct / 2
    return PriceZone(low=price - half, high=price + half)


def reference_levels(values: dict[str, float | None], timestamp: datetime) -> list[Level]:
    levels: list[Level] = []
    for kind, price in values.items():
        if price is not None:
            levels.append(Level(type=kind, zone=make_zone(price), source=kind, timestamp=timestamp))
    return levels


def swing_levels(candles: Sequence[CandleEvent], window: int = 2) -> list[Level]:
    result: list[Level] = []
    if len(candles) < window * 2 + 1:
        return result
    for index in range(window, len(candles) - window):
        candidate = candles[index]
        nearby = list(candles[index - window : index]) + list(
            candles[index + 1 : index + window + 1]
        )
        if all(candidate.high > c.high for c in nearby):
            result.append(
                Level(
                    type="SWING_HIGH",
                    zone=make_zone(candidate.high),
                    source="recent_candles",
                    timestamp=candidate.timestamp,
                )
            )
        if all(candidate.low < c.low for c in nearby):
            result.append(
                Level(
                    type="SWING_LOW",
                    zone=make_zone(candidate.low),
                    source="recent_candles",
                    timestamp=candidate.timestamp,
                )
            )
    return result


def nearest_levels(price: float, levels: Iterable[Level]) -> tuple[Level | None, Level | None]:
    below = [level for level in levels if level.zone.high <= price]
    above = [level for level in levels if level.zone.low >= price]
    support = max(below, key=lambda level: level.zone.high, default=None)
    resistance = min(above, key=lambda level: level.zone.low, default=None)
    return support, resistance
