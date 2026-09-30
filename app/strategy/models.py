from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DataQuality(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    STALE = "STALE"


class Direction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class SetupState(StrEnum):
    WATCHING = "WATCHING"
    ARMED = "ARMED"
    WAITING_FOR_RETEST = "WAITING_FOR_RETEST"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXTENDED = "EXTENDED"


class FeatureValue(BaseModel):
    value: float | None = None
    quality: DataQuality
    as_of: datetime | None = None
    reason: str | None = None


class PriceZone(BaseModel):
    model_config = ConfigDict(frozen=True)
    low: float
    high: float

    def contains(self, price: float, tolerance: float = 0) -> bool:
        return self.low - tolerance <= price <= self.high + tolerance


class Level(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    zone: PriceZone
    source: str
    confirmations: int = Field(default=1, ge=1)
    timestamp: datetime

    @property
    def midpoint(self) -> float:
        return (self.zone.low + self.zone.high) / 2


class BenchmarkContext(BaseModel):
    symbol: str
    direction: Direction | None = None
    above_vwap: bool | None = None
    momentum: float | None = None
    opening_range_status: str | None = None
    quality: DataQuality = DataQuality.PARTIAL


class FeatureSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    price: FeatureValue
    bid: FeatureValue = Field(default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL))
    ask: FeatureValue = Field(default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL))
    spread: FeatureValue = Field(default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL))
    vwap: FeatureValue = Field(default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL))
    distance_from_vwap: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    session_high: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    session_low: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    premarket_high: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    premarket_low: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    previous_day_high: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    previous_day_low: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    previous_close: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    opening_range_5_high: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    opening_range_5_low: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    opening_range_15_high: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    opening_range_15_low: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    rolling_volume: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    relative_volume: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    momentum: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    support_distance: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    resistance_distance: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    spy_relative_strength: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )
    qqq_relative_strength: FeatureValue = Field(
        default_factory=lambda: FeatureValue(quality=DataQuality.PARTIAL)
    )


class StrategyObservation(BaseModel):
    symbol: str
    timestamp: datetime
    direction: Direction
    price: float
    candle_open: float
    candle_high: float
    candle_low: float
    level: Level | None
    vwap: float | None = None
    relative_volume: float | None = None
    relative_strength: float | None = None
    momentum: float | None = None
    volume_confirmed: bool = False
    market_agrees: bool | None = None
    opening_range_context: bool = False
    reward_risk: float | None = None
    next_level: float | None = None


class StateTransition(BaseModel):
    symbol: str
    timestamp: datetime
    from_state: SetupState
    to_state: SetupState
    direction: Direction
    reason: str
    strategy_name: str
    strategy_version: str
    observation: StrategyObservation


class Signal(BaseModel):
    symbol: str
    timestamp: datetime
    direction: Direction
    state: SetupState
    strategy_name: str
    strategy_version: str
    price: float
    invalidation: float | None
    target_1: float | None
    target_2: float | None
    reasons: list[str]
