from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.strategy.levels import make_zone
from app.strategy.models import Direction, Level


class TriggerPlan(BaseModel):
    price: float = Field(gt=0)
    direction: Direction = Direction.BULLISH
    targets: list[float] = Field(default_factory=list, max_length=2)

    @field_validator("targets")
    @classmethod
    def valid_directional_targets(cls, targets: list[float], info) -> list[float]:
        if any(target <= 0 for target in targets):
            raise ValueError("planned targets must be positive")
        trigger = info.data.get("price")
        direction = info.data.get("direction", Direction.BULLISH)
        if trigger is not None:
            if direction == Direction.BULLISH and any(target <= trigger for target in targets):
                raise ValueError("bullish targets must be above the trigger")
            if direction == Direction.BEARISH and any(target >= trigger for target in targets):
                raise ValueError("bearish targets must be below the trigger")
        return targets


class SymbolPlan(BaseModel):
    note: str
    triggers: list[TriggerPlan] = Field(min_length=1)


class WatchlistContext(BaseModel):
    session_date: date
    plans: dict[str, SymbolPlan]

    @field_validator("plans")
    @classmethod
    def normalize_symbols(cls, value: dict[str, SymbolPlan]) -> dict[str, SymbolPlan]:
        return {symbol.strip().upper(): plan for symbol, plan in value.items()}

    def levels_for(self, symbol: str, timestamp: datetime, session_date: date) -> list[Level]:
        if session_date != self.session_date:
            return []
        plan = self.plans.get(symbol.upper())
        if plan is None:
            return []
        return [
            Level(
                type="MANUAL_DECISION_LEVEL",
                zone=make_zone(trigger.price),
                source=f"watchlist_context:{plan.note}",
                timestamp=timestamp,
            )
            for trigger in plan.triggers
        ]

    def trigger_for_level(self, symbol: str, level: Level) -> TriggerPlan | None:
        if not level.source.startswith("watchlist_context:"):
            return None
        plan = self.plans.get(symbol.upper())
        if plan is None:
            return None
        return min(
            plan.triggers,
            key=lambda trigger: abs(trigger.price - level.midpoint),
            default=None,
        )


def load_watchlist_context(path: Path) -> WatchlistContext | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WatchlistContext.model_validate(payload)
