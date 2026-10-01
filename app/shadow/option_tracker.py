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
    opening_commission_per_contract: float = 1.0
    estimated_opening_fees_per_contract: float = 0.15
    estimated_closing_fees_per_contract: float = 0.15
    additional_slippage_price_per_side: float = 0.01
    latest_at: datetime | None = None
    latest_bid: float | None = None
    latest_ask: float | None = None
    latest_mid: float | None = None
    maximum_favorable_pnl: float = 0
    maximum_adverse_pnl: float = 0
    maximum_favorable_net_pnl: float = 0
    maximum_adverse_net_pnl: float = 0
    marks_after: dict[str, float] = Field(default_factory=dict)
    status: str = "OPEN"
    closed_at: datetime | None = None
    exit_reason: str | None = None
    exit_fill: float | None = None
    realized_pnl: float | None = None
    realized_return_pct: float | None = None
    net_realized_pnl: float | None = None
    net_realized_return_pct: float | None = None
    pending_exit_reason: str | None = None
    pending_exit_at: datetime | None = None
    exit_delay_seconds: float | None = None

    @property
    def cost(self) -> float:
        return self.entry_fill * self.multiplier * self.quantity

    def unrealized_pnl(self, liquidation_price: float | None = None) -> float | None:
        price = self.latest_bid if liquidation_price is None else liquidation_price
        if price is None:
            return None
        return (price - self.entry_fill) * self.multiplier * self.quantity

    @property
    def estimated_round_trip_cost(self) -> float:
        fixed = self.quantity * (
            self.opening_commission_per_contract
            + self.estimated_opening_fees_per_contract
            + self.estimated_closing_fees_per_contract
        )
        slippage = (
            self.additional_slippage_price_per_side * 2 * self.multiplier * self.quantity
        )
        return fixed + slippage

    @property
    def net_cost_basis(self) -> float:
        opening_cost = self.quantity * (
            self.opening_commission_per_contract
            + self.estimated_opening_fees_per_contract
            + self.additional_slippage_price_per_side * self.multiplier
        )
        return self.cost + opening_cost

    def net_unrealized_pnl(self, liquidation_price: float | None = None) -> float | None:
        gross = self.unrealized_pnl(liquidation_price)
        return None if gross is None else gross - self.estimated_round_trip_cost


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
            opening_commission_per_contract=self.settings.opening_commission_per_contract,
            estimated_opening_fees_per_contract=(
                self.settings.estimated_opening_fees_per_contract
            ),
            estimated_closing_fees_per_contract=(
                self.settings.estimated_closing_fees_per_contract
            ),
            additional_slippage_price_per_side=(
                self.settings.additional_slippage_price_per_side
            ),
        )
        self.active[signal.symbol] = record
        return record

    def restore(self, record: ShadowOptionTradeRecord) -> None:
        if record.status == "OPEN":
            self.active[record.underlying_symbol] = record

    def update(
        self,
        candle: CandleEvent,
        quote: QuoteEvent | None,
        underlying: ShadowTradeRecord,
    ) -> dict[str, object] | None:
        record = self.active.get(candle.symbol)
        if record is None or record.status != "OPEN" or candle.timestamp <= record.signal_at:
            return None
        intended_at = candle.timestamp + timedelta(seconds=candle.interval_seconds)
        exit_reason = _required_exit_reason(candle, underlying, record, self.settings)
        newly_pending = exit_reason is not None and record.pending_exit_reason is None
        if newly_pending:
            record.pending_exit_reason = exit_reason
            record.pending_exit_at = intended_at

        execution_event: dict[str, object] | None = None
        quote_problem = _quote_problem(quote, intended_at, self.settings)
        if quote_problem is not None:
            if newly_pending:
                execution_event = {
                    "stage": "EXIT",
                    "status": "MISSED",
                    "reason_code": quote_problem,
                    "intended_at": intended_at.isoformat(),
                    "delay_seconds": None,
                }
            return option_outcome(
                record,
                candle.close,
                mark_quality=quote_problem,
                execution_event=execution_event,
            )

        assert quote is not None and quote.bid is not None and quote.ask is not None
        bid = quote.bid
        ask = quote.ask
        mid = (bid + ask) / 2
        record.latest_at = quote.timestamp
        record.latest_bid = bid
        record.latest_ask = ask
        record.latest_mid = mid
        pnl = record.unrealized_pnl(bid)
        net_pnl = record.net_unrealized_pnl(bid)
        assert pnl is not None
        assert net_pnl is not None
        record.maximum_favorable_pnl = max(record.maximum_favorable_pnl, pnl)
        record.maximum_adverse_pnl = max(record.maximum_adverse_pnl, -pnl)
        record.maximum_favorable_net_pnl = max(record.maximum_favorable_net_pnl, net_pnl)
        record.maximum_adverse_net_pnl = max(record.maximum_adverse_net_pnl, -net_pnl)
        elapsed = candle.timestamp - record.signal_at
        for minute in self.checkpoints:
            key = f"{minute}m"
            if elapsed >= timedelta(minutes=minute) and key not in record.marks_after:
                record.marks_after[key] = bid

        if record.pending_exit_reason and record.pending_exit_at:
            delay = max((quote.timestamp - record.pending_exit_at).total_seconds(), 0)
            record.status = "CLOSED"
            record.closed_at = quote.timestamp
            record.exit_reason = record.pending_exit_reason
            record.exit_fill = bid
            record.realized_pnl = pnl
            record.realized_return_pct = pnl / record.cost if record.cost else None
            record.net_realized_pnl = net_pnl
            record.net_realized_return_pct = (
                net_pnl / record.net_cost_basis if record.net_cost_basis else None
            )
            record.exit_delay_seconds = delay
            execution_status = (
                "DELAYED"
                if delay > self.settings.execution_delay_tolerance_seconds
                else "SUCCESS"
            )
            execution_event = {
                "stage": "EXIT",
                "status": execution_status,
                "reason_code": (
                    "LATE_VALID_QUOTE" if execution_status == "DELAYED" else "VALID_QUOTE"
                ),
                "intended_at": record.pending_exit_at.isoformat(),
                "delay_seconds": delay,
            }
            self.active.pop(candle.symbol, None)
        return option_outcome(
            record,
            candle.close,
            mark_quality="READY",
            execution_event=execution_event,
        )


def option_outcome(
    record: ShadowOptionTradeRecord,
    underlying_price: float,
    *,
    mark_quality: str = "READY",
    execution_event: dict[str, object] | None = None,
) -> dict[str, object]:
    unrealized = record.unrealized_pnl()
    net_unrealized = record.net_unrealized_pnl()
    return {
        "underlying_price": underlying_price,
        "option_bid": record.latest_bid,
        "option_ask": record.latest_ask,
        "option_mid": record.latest_mid,
        "liquidation_price": record.latest_bid,
        "unrealized_pnl": unrealized,
        "unrealized_return_pct": unrealized / record.cost if unrealized is not None else None,
        "net_unrealized_pnl": net_unrealized,
        "net_unrealized_return_pct": (
            net_unrealized / record.net_cost_basis if net_unrealized is not None else None
        ),
        "estimated_round_trip_cost": record.estimated_round_trip_cost,
        "maximum_favorable_pnl": record.maximum_favorable_pnl,
        "maximum_adverse_pnl": record.maximum_adverse_pnl,
        "maximum_favorable_net_pnl": record.maximum_favorable_net_pnl,
        "maximum_adverse_net_pnl": record.maximum_adverse_net_pnl,
        "marks_after": record.marks_after,
        "status": record.status,
        "closed_at": record.closed_at.isoformat() if record.closed_at else None,
        "exit_reason": record.exit_reason,
        "exit_fill": record.exit_fill,
        "realized_pnl": record.realized_pnl,
        "realized_return_pct": record.realized_return_pct,
        "net_realized_pnl": record.net_realized_pnl,
        "net_realized_return_pct": record.net_realized_return_pct,
        "pending_exit_reason": record.pending_exit_reason,
        "pending_exit_at": (
            record.pending_exit_at.isoformat() if record.pending_exit_at else None
        ),
        "exit_delay_seconds": record.exit_delay_seconds,
        "mark_quality": mark_quality,
        "execution_event": execution_event,
    }


def _required_exit_reason(
    candle: CandleEvent,
    underlying: ShadowTradeRecord,
    record: ShadowOptionTradeRecord,
    settings: OptionSettings,
) -> str | None:
    if record.pending_exit_reason:
        return record.pending_exit_reason
    elapsed = candle.timestamp - record.signal_at
    if underlying.stop_hit_time == candle.timestamp:
        return "UNDERLYING_INVALIDATION"
    if (
        underlying.time_to_target_1_seconds is not None
        and underlying.time_to_target_1_seconds == elapsed.total_seconds()
    ):
        return "UNDERLYING_TARGET_1"
    if elapsed >= timedelta(minutes=settings.maximum_holding_minutes):
        return "TIME_EXIT"
    return None


def _quote_problem(
    quote: QuoteEvent | None, intended_at: datetime, settings: OptionSettings
) -> str | None:
    if quote is None:
        return "NO_OPTION_QUOTE"
    if quote.bid is None or quote.ask is None or quote.bid < 0 or quote.ask < quote.bid:
        return "INVALID_OPTION_QUOTE"
    age_seconds = (intended_at - quote.timestamp).total_seconds()
    if age_seconds > settings.maximum_option_quote_age_seconds:
        return "STALE_OPTION_QUOTE"
    return None
