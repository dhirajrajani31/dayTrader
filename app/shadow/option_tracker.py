from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.config.settings import OptionSettings
from app.market_data.models import CandleEvent, QuoteEvent
from app.options.models import OptionCandidate
from app.shadow.tracker import ShadowTradeRecord
from app.strategy.models import Direction, Signal


class ShadowOptionTradeRecord(BaseModel):
    execution_policy_version: str = "OPTION_SHADOW_V1"
    option_trade_id: int | None = None
    shadow_trade_id: int
    underlying_symbol: str
    direction: Direction
    signal_at: datetime
    option_symbol: str
    streamer_symbol: str
    expiration: datetime
    days_to_expiration: int
    strike: float
    call_put: str
    quantity: int = 1
    multiplier: int = 100
    opened_at: datetime
    entry_bid: float | None = None
    entry_ask: float
    entry_mid: float | None = None
    entry_fill: float
    entry_delta: float | None = None
    entry_gamma: float | None = None
    entry_theta: float | None = None
    entry_iv: float | None = None
    entry_open_interest: int | None = None
    entry_volume: int | None = None
    latest_at: datetime | None = None
    latest_bid: float | None = None
    latest_ask: float | None = None
    latest_mid: float | None = None
    maximum_favorable_pnl: float = 0
    maximum_adverse_pnl: float = 0
    marks_after: dict[str, float] = Field(default_factory=dict)
    status: str = "OPEN"
    closed_at: datetime | None = None
    exit_reason: str | None = None
    exit_fill: float | None = None
    realized_pnl: float | None = None
    realized_return_pct: float | None = None

    @property
    def cost(self) -> float:
        return self.entry_fill * self.multiplier * self.quantity

    def unrealized_pnl(self, liquidation_price: float | None = None) -> float | None:
        price = self.latest_bid if liquidation_price is None else liquidation_price
        if price is None:
            return None
        return (price - self.entry_fill) * self.multiplier * self.quantity


class OptionShadowTracker:
    checkpoints = (5, 15, 30, 60)

    def __init__(self, settings: OptionSettings):
        self.settings = settings
        self.active: dict[str, ShadowOptionTradeRecord] = {}

    def open(
        self, shadow_trade_id: int, signal: Signal, candidate: OptionCandidate
    ) -> ShadowOptionTradeRecord:
        if candidate.ask is None or candidate.ask <= 0:
            raise ValueError("option shadow entry requires a positive displayed ask")
        opened_at = candidate.quote_timestamp or signal.timestamp
        record = ShadowOptionTradeRecord(
            execution_policy_version=self.settings.execution_policy_version,
            shadow_trade_id=shadow_trade_id,
            underlying_symbol=signal.symbol,
            direction=signal.direction,
            signal_at=signal.timestamp,
            option_symbol=candidate.symbol,
            streamer_symbol=candidate.streamer_symbol,
            expiration=candidate.expiration,
            days_to_expiration=candidate.days_to_expiration,
            strike=candidate.strike,
            call_put=candidate.call_put,
            quantity=self.settings.quantity,
            multiplier=candidate.shares_per_contract or self.settings.contract_multiplier,
            opened_at=opened_at,
            entry_bid=candidate.bid,
            entry_ask=candidate.ask,
            entry_mid=candidate.mid,
            entry_fill=candidate.ask,
            entry_delta=candidate.delta,
            entry_gamma=candidate.gamma,
            entry_theta=candidate.theta,
            entry_iv=candidate.iv,
            entry_open_interest=candidate.open_interest,
            entry_volume=candidate.volume,
        )
        self.active[signal.symbol] = record
        return record

    def restore(self, record: ShadowOptionTradeRecord) -> None:
        if record.status == "OPEN":
            self.active[record.underlying_symbol] = record

    def update(
        self,
        candle: CandleEvent,
        quote: QuoteEvent,
        underlying: ShadowTradeRecord,
    ) -> dict[str, object] | None:
        record = self.active.get(candle.symbol)
        if record is None or record.status != "OPEN" or candle.timestamp <= record.signal_at:
            return None
        bid = quote.bid
        ask = quote.ask
        if bid is None or ask is None or bid < 0 or ask < bid:
            return None
        mid = (bid + ask) / 2
        record.latest_at = quote.timestamp
        record.latest_bid = bid
        record.latest_ask = ask
        record.latest_mid = mid
        pnl = record.unrealized_pnl(bid)
        assert pnl is not None
        record.maximum_favorable_pnl = max(record.maximum_favorable_pnl, pnl)
        record.maximum_adverse_pnl = max(record.maximum_adverse_pnl, -pnl)
        elapsed = candle.timestamp - record.signal_at
        for minute in self.checkpoints:
            key = f"{minute}m"
            if elapsed >= timedelta(minutes=minute) and key not in record.marks_after:
                record.marks_after[key] = bid

        stop_now = underlying.stop_hit_time == candle.timestamp
        target_now = (
            underlying.time_to_target_1_seconds is not None
            and underlying.time_to_target_1_seconds == elapsed.total_seconds()
        )
        exit_reason = None
        if stop_now:
            exit_reason = "UNDERLYING_INVALIDATION"
        elif target_now:
            exit_reason = "UNDERLYING_TARGET_1"
        elif elapsed >= timedelta(minutes=self.settings.maximum_holding_minutes):
            exit_reason = "TIME_EXIT"
        if exit_reason:
            record.status = "CLOSED"
            record.closed_at = quote.timestamp
            record.exit_reason = exit_reason
            record.exit_fill = bid
            record.realized_pnl = pnl
            record.realized_return_pct = pnl / record.cost if record.cost else None
            self.active.pop(candle.symbol, None)
        return option_outcome(record, candle.close)


def option_outcome(record: ShadowOptionTradeRecord, underlying_price: float) -> dict[str, object]:
    unrealized = record.unrealized_pnl()
    return {
        "underlying_price": underlying_price,
        "option_bid": record.latest_bid,
        "option_ask": record.latest_ask,
        "option_mid": record.latest_mid,
        "liquidation_price": record.latest_bid,
        "unrealized_pnl": unrealized,
        "unrealized_return_pct": unrealized / record.cost if unrealized is not None else None,
        "maximum_favorable_pnl": record.maximum_favorable_pnl,
        "maximum_adverse_pnl": record.maximum_adverse_pnl,
        "marks_after": record.marks_after,
        "status": record.status,
        "closed_at": record.closed_at.isoformat() if record.closed_at else None,
        "exit_reason": record.exit_reason,
        "exit_fill": record.exit_fill,
        "realized_pnl": record.realized_pnl,
        "realized_return_pct": record.realized_return_pct,
    }
