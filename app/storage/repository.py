from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.shadow.option_tracker import ShadowOptionTradeRecord
from app.shadow.tracker import ShadowTradeRecord
from app.storage.models import (
    ApplicationEventRow,
    MarketFeatureSnapshotRow,
    ShadowExecutionEventRow,
    ShadowOptionMarkRow,
    ShadowOptionTradeRow,
    ShadowTradeOutcomeRow,
    ShadowTradeRow,
    SignalRow,
    StateTransitionRow,
    StrategyStateRow,
    WatchlistSessionRow,
)
from app.strategy.models import Direction, Signal, StateTransition, StrategyObservation


class Repository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def save_watchlist(
        self, symbols: list[str], timestamp: datetime, source_tag: str = "manual"
    ) -> None:
        with self.sessions.begin() as db:
            db.add(
                WatchlistSessionRow(created_at=timestamp, symbols=symbols, source_tag=source_tag)
            )

    def save_transition(self, transition: StateTransition) -> None:
        payload = transition.observation.model_dump(mode="json")
        with self.sessions.begin() as db:
            db.add(
                StateTransitionRow(
                    symbol=transition.symbol,
                    timestamp=transition.timestamp,
                    from_state=transition.from_state.value,
                    to_state=transition.to_state.value,
                    direction=transition.direction.value,
                    reason=transition.reason,
                    strategy_name=transition.strategy_name,
                    strategy_version=transition.strategy_version,
                    snapshot=payload,
                )
            )
            existing = db.scalar(
                select(StrategyStateRow).where(
                    StrategyStateRow.symbol == transition.symbol,
                    StrategyStateRow.direction == transition.direction.value,
                    StrategyStateRow.strategy_version == transition.strategy_version,
                )
            )
            if existing:
                existing.state = transition.to_state.value
                existing.updated_at = transition.timestamp
            else:
                db.add(
                    StrategyStateRow(
                        symbol=transition.symbol,
                        direction=transition.direction.value,
                        state=transition.to_state.value,
                        strategy_name=transition.strategy_name,
                        strategy_version=transition.strategy_version,
                        updated_at=transition.timestamp,
                    )
                )

    def save_feature_snapshot(self, payload: dict) -> None:
        with self.sessions.begin() as db:
            db.add(
                MarketFeatureSnapshotRow(
                    symbol=payload["symbol"],
                    timestamp=datetime.fromisoformat(payload["timestamp"]),
                    quality=payload["price"]["quality"],
                    features=payload,
                )
            )

    def save_signal(self, signal: Signal) -> None:
        with self.sessions.begin() as db:
            db.add(
                SignalRow(
                    symbol=signal.symbol,
                    timestamp=signal.timestamp,
                    direction=signal.direction.value,
                    strategy_name=signal.strategy_name,
                    strategy_version=signal.strategy_version,
                    payload=signal.model_dump(mode="json"),
                )
            )

    def save_shadow_trade(self, signal: Signal, context: dict) -> int:
        with self.sessions.begin() as db:
            row = ShadowTradeRow(
                symbol=signal.symbol,
                direction=signal.direction.value,
                opened_at=signal.timestamp,
                strategy_name=signal.strategy_name,
                strategy_version=signal.strategy_version,
                entry_underlying=signal.price,
                invalidation_underlying=signal.invalidation,
                target_1=signal.target_1,
                target_2=signal.target_2,
                entry_context=context,
            )
            db.add(row)
            db.flush()
            return row.id

    def save_outcome(self, trade_id: int, timestamp: datetime, payload: dict) -> None:
        with self.sessions.begin() as db:
            db.add(
                ShadowTradeOutcomeRow(
                    shadow_trade_id=trade_id, updated_at=timestamp, payload=payload
                )
            )

    def save_option_trade(self, record: ShadowOptionTradeRecord, selection_payload: dict) -> int:
        with self.sessions.begin() as db:
            row = ShadowOptionTradeRow(
                shadow_trade_id=record.shadow_trade_id,
                underlying_symbol=record.underlying_symbol,
                direction=record.direction.value,
                signal_at=record.signal_at,
                option_symbol=record.option_symbol,
                streamer_symbol=record.streamer_symbol,
                expiration=record.expiration,
                days_to_expiration=record.days_to_expiration,
                strike=record.strike,
                call_put=record.call_put,
                quantity=record.quantity,
                multiplier=record.multiplier,
                opened_at=record.opened_at,
                entry_bid=record.entry_bid,
                entry_ask=record.entry_ask,
                entry_mid=record.entry_mid,
                entry_fill=record.entry_fill,
                entry_delta=record.entry_delta,
                entry_gamma=record.entry_gamma,
                entry_theta=record.entry_theta,
                entry_iv=record.entry_iv,
                entry_open_interest=record.entry_open_interest,
                entry_volume=record.entry_volume,
                status=record.status,
                selection_payload={
                    **selection_payload,
                    "opening_commission_per_contract": (
                        record.opening_commission_per_contract
                    ),
                    "estimated_opening_fees_per_contract": (
                        record.estimated_opening_fees_per_contract
                    ),
                    "estimated_closing_fees_per_contract": (
                        record.estimated_closing_fees_per_contract
                    ),
                    "additional_slippage_price_per_side": (
                        record.additional_slippage_price_per_side
                    ),
                },
            )
            db.add(row)
            db.flush()
            return row.id

    def save_option_mark(
        self, option_trade_id: int, timestamp: datetime, payload: dict[str, object]
    ) -> None:
        with self.sessions.begin() as db:
            db.add(
                ShadowOptionMarkRow(
                    shadow_option_trade_id=option_trade_id,
                    timestamp=timestamp,
                    underlying_price=_required_float(payload["underlying_price"]),
                    option_bid=_optional_float(payload.get("option_bid")),
                    option_ask=_optional_float(payload.get("option_ask")),
                    option_mid=_optional_float(payload.get("option_mid")),
                    liquidation_price=_optional_float(payload.get("liquidation_price")),
                    unrealized_pnl=_optional_float(payload.get("unrealized_pnl")),
                    unrealized_return_pct=_optional_float(payload.get("unrealized_return_pct")),
                    payload=payload,
                )
            )
            row = db.get(ShadowOptionTradeRow, option_trade_id)
            if row is not None and payload.get("status") == "CLOSED":
                row.status = "CLOSED"
                row.closed_at = _optional_datetime(payload.get("closed_at"))
                row.exit_reason = str(payload.get("exit_reason") or "UNKNOWN")
                row.exit_fill = _optional_float(payload.get("exit_fill"))
                row.realized_pnl = _optional_float(payload.get("realized_pnl"))
                row.realized_return_pct = _optional_float(payload.get("realized_return_pct"))

    def save_execution_event(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        stage: str,
        status: str,
        reason_code: str,
        shadow_trade_id: int | None = None,
        shadow_option_trade_id: int | None = None,
        intended_at: datetime | None = None,
        delay_seconds: float | None = None,
        payload: dict | None = None,
    ) -> None:
        with self.sessions.begin() as db:
            db.add(
                ShadowExecutionEventRow(
                    shadow_trade_id=shadow_trade_id,
                    shadow_option_trade_id=shadow_option_trade_id,
                    symbol=symbol,
                    timestamp=timestamp,
                    stage=stage,
                    status=status,
                    reason_code=reason_code,
                    intended_at=intended_at,
                    delay_seconds=delay_seconds,
                    payload=payload or {},
                )
            )

    def load_active_shadow_trades(
        self, opened_since: datetime
    ) -> list[tuple[int, ShadowTradeRecord]]:
        with self.sessions() as db:
            trades = db.scalars(select(ShadowTradeRow)).all()
            open_option_shadow_ids = set(
                db.scalars(
                    select(ShadowOptionTradeRow.shadow_trade_id).where(
                        ShadowOptionTradeRow.status == "OPEN"
                    )
                ).all()
            )
            outcomes = db.scalars(
                select(ShadowTradeOutcomeRow).order_by(ShadowTradeOutcomeRow.id)
            ).all()
            latest = {row.shadow_trade_id: row.payload for row in outcomes}
            restored: list[tuple[int, ShadowTradeRecord]] = []
            for row in trades:
                if row.opened_at < opened_since and row.id not in open_option_shadow_ids:
                    continue
                payload = latest.get(row.id, {})
                if payload.get("tracking_complete") and row.id not in open_option_shadow_ids:
                    continue
                record = ShadowTradeRecord(
                    symbol=row.symbol,
                    direction=Direction(row.direction),
                    opened_at=_aware_utc(row.opened_at),
                    strategy_name=row.strategy_name,
                    strategy_version=row.strategy_version,
                    entry_underlying=row.entry_underlying,
                    invalidation_underlying=row.invalidation_underlying,
                    target_1=row.target_1,
                    target_2=row.target_2,
                    entry_context=row.entry_context,
                    prices_after=payload.get("prices_after", {}),
                    maximum_favorable_excursion=payload.get("mfe", 0),
                    maximum_adverse_excursion=payload.get("mae", 0),
                    stop_hit=payload.get("stop_hit", False),
                    stop_hit_time=_optional_datetime(payload.get("stop_hit_time")),
                    target_1_hit=payload.get("target_1_hit", False),
                    target_2_hit=payload.get("target_2_hit", False),
                    time_to_target_1_seconds=payload.get("time_to_target_1_seconds"),
                    time_to_target_2_seconds=payload.get("time_to_target_2_seconds"),
                    tracking_complete=payload.get("tracking_complete", False),
                )
                restored.append((row.id, record))
            return restored

    def load_active_option_trades(self) -> list[ShadowOptionTradeRecord]:
        with self.sessions() as db:
            rows = db.scalars(
                select(ShadowOptionTradeRow).where(ShadowOptionTradeRow.status == "OPEN")
            ).all()
            marks = db.scalars(select(ShadowOptionMarkRow).order_by(ShadowOptionMarkRow.id)).all()
            latest = {row.shadow_option_trade_id: row for row in marks}
            records: list[ShadowOptionTradeRecord] = []
            for row in rows:
                latest_mark = latest.get(row.id)
                payload = latest_mark.payload if latest_mark else {}
                record = ShadowOptionTradeRecord(
                    execution_policy_version=str(
                        row.selection_payload.get(
                            "execution_policy_version", "OPTION_SHADOW_V1"
                        )
                    ),
                    option_trade_id=row.id,
                    shadow_trade_id=row.shadow_trade_id,
                    underlying_symbol=row.underlying_symbol,
                    direction=Direction(row.direction),
                    signal_at=_aware_utc(row.signal_at),
                    option_symbol=row.option_symbol,
                    streamer_symbol=row.streamer_symbol,
                    expiration=_aware_utc(row.expiration),
                    days_to_expiration=row.days_to_expiration,
                    strike=row.strike,
                    call_put=row.call_put,
                    quantity=row.quantity,
                    multiplier=row.multiplier,
                    opened_at=_aware_utc(row.opened_at),
                    entry_bid=row.entry_bid,
                    entry_ask=row.entry_ask,
                    entry_mid=row.entry_mid,
                    entry_fill=row.entry_fill,
                    entry_delta=row.entry_delta,
                    entry_gamma=row.entry_gamma,
                    entry_theta=row.entry_theta,
                    entry_iv=row.entry_iv,
                    entry_open_interest=row.entry_open_interest,
                    entry_volume=row.entry_volume,
                    opening_commission_per_contract=_required_float(
                        row.selection_payload.get("opening_commission_per_contract", 1.0)
                    ),
                    estimated_opening_fees_per_contract=_required_float(
                        row.selection_payload.get("estimated_opening_fees_per_contract", 0.15)
                    ),
                    estimated_closing_fees_per_contract=_required_float(
                        row.selection_payload.get("estimated_closing_fees_per_contract", 0.15)
                    ),
                    additional_slippage_price_per_side=_required_float(
                        row.selection_payload.get("additional_slippage_price_per_side", 0.01)
                    ),
                    latest_at=_aware_utc(latest_mark.timestamp) if latest_mark else None,
                    latest_bid=_optional_float(payload.get("option_bid")),
                    latest_ask=_optional_float(payload.get("option_ask")),
                    latest_mid=_optional_float(payload.get("option_mid")),
                    maximum_favorable_pnl=_required_float(payload.get("maximum_favorable_pnl", 0)),
                    maximum_adverse_pnl=_required_float(payload.get("maximum_adverse_pnl", 0)),
                    maximum_favorable_net_pnl=_required_float(
                        payload.get("maximum_favorable_net_pnl", 0)
                    ),
                    maximum_adverse_net_pnl=_required_float(
                        payload.get("maximum_adverse_net_pnl", 0)
                    ),
                    marks_after=payload.get("marks_after", {}),
                    pending_exit_reason=(
                        str(payload["pending_exit_reason"])
                        if payload.get("pending_exit_reason")
                        else None
                    ),
                    pending_exit_at=_optional_datetime(payload.get("pending_exit_at")),
                )
                records.append(record)
            return records

    def load_latest_transitions_since(
        self, since: datetime, strategy_version: str
    ) -> list[StateTransition]:
        with self.sessions() as db:
            rows = db.scalars(
                select(StateTransitionRow)
                .where(
                    StateTransitionRow.timestamp >= since,
                    StateTransitionRow.strategy_version == strategy_version,
                )
                .order_by(StateTransitionRow.id)
            ).all()
            latest: dict[tuple[str, str], StateTransitionRow] = {
                (row.symbol, row.direction): row for row in rows
            }
            return [
                StateTransition(
                    symbol=row.symbol,
                    timestamp=StrategyObservation.model_validate(row.snapshot).timestamp,
                    from_state=row.from_state,
                    to_state=row.to_state,
                    direction=row.direction,
                    reason=row.reason,
                    strategy_name=row.strategy_name,
                    strategy_version=row.strategy_version,
                    observation=StrategyObservation.model_validate(row.snapshot),
                )
                for row in latest.values()
            ]

    def recent_trade_report(self, limit: int = 20) -> list[dict[str, object]]:
        with self.sessions() as db:
            trades = db.scalars(
                select(ShadowTradeRow).order_by(ShadowTradeRow.id.desc()).limit(limit)
            ).all()
            option_rows = db.scalars(select(ShadowOptionTradeRow)).all()
            option_by_trade = {row.shadow_trade_id: row for row in option_rows}
            marks = db.scalars(select(ShadowOptionMarkRow).order_by(ShadowOptionMarkRow.id)).all()
            latest_marks = {row.shadow_option_trade_id: row for row in marks}
            outcomes = db.scalars(
                select(ShadowTradeOutcomeRow).order_by(ShadowTradeOutcomeRow.id)
            ).all()
            latest_outcomes = {row.shadow_trade_id: row.payload for row in outcomes}
            result: list[dict[str, object]] = []
            for trade in trades:
                option = option_by_trade.get(trade.id)
                mark = latest_marks.get(option.id) if option else None
                result.append(
                    {
                        "id": trade.id,
                        "symbol": trade.symbol,
                        "direction": trade.direction,
                        "opened_at": trade.opened_at,
                        "entry_underlying": trade.entry_underlying,
                        "invalidation_underlying": trade.invalidation_underlying,
                        "target_1": trade.target_1,
                        "target_2": trade.target_2,
                        "underlying_outcome": latest_outcomes.get(trade.id, {}),
                        "option": _option_report(option, mark),
                    }
                )
            return result

    def evaluation_dataset(self) -> dict[str, list[dict[str, object]]]:
        """Return normalized rows for deterministic scorecard calculations."""
        with self.sessions() as db:
            signals = db.scalars(select(SignalRow).order_by(SignalRow.timestamp)).all()
            transitions = db.scalars(
                select(StateTransitionRow).order_by(StateTransitionRow.timestamp)
            ).all()
            options = db.scalars(
                select(ShadowOptionTradeRow).order_by(ShadowOptionTradeRow.signal_at)
            ).all()
            marks = db.scalars(
                select(ShadowOptionMarkRow).order_by(ShadowOptionMarkRow.id)
            ).all()
            events = db.scalars(
                select(ShadowExecutionEventRow).order_by(ShadowExecutionEventRow.timestamp)
            ).all()
            latest_marks = {row.shadow_option_trade_id: row for row in marks}
            return {
                "signals": [
                    {
                        "id": row.id,
                        "symbol": row.symbol,
                        "timestamp": row.timestamp,
                        "direction": row.direction,
                        "strategy_version": row.strategy_version,
                    }
                    for row in signals
                ],
                "transitions": [
                    {
                        "symbol": row.symbol,
                        "timestamp": row.timestamp,
                        "to_state": row.to_state,
                        "strategy_version": row.strategy_version,
                    }
                    for row in transitions
                ],
                "option_trades": [
                    {
                        "id": row.id,
                        "shadow_trade_id": row.shadow_trade_id,
                        "symbol": row.underlying_symbol,
                        "signal_at": row.signal_at,
                        "status": row.status,
                        "exit_reason": row.exit_reason,
                        "gross_realized_pnl": row.realized_pnl,
                        "latest_mark": (
                            latest_marks[row.id].payload if row.id in latest_marks else None
                        ),
                    }
                    for row in options
                ],
                "execution_events": [
                    {
                        "shadow_trade_id": row.shadow_trade_id,
                        "shadow_option_trade_id": row.shadow_option_trade_id,
                        "symbol": row.symbol,
                        "timestamp": row.timestamp,
                        "stage": row.stage,
                        "status": row.status,
                        "reason_code": row.reason_code,
                        "delay_seconds": row.delay_seconds,
                    }
                    for row in events
                ],
                "option_marks": [
                    {
                        "shadow_option_trade_id": row.shadow_option_trade_id,
                        "timestamp": row.timestamp,
                        "mark_quality": row.payload.get("mark_quality", "UNKNOWN"),
                    }
                    for row in marks
                ],
            }

    def application_event(
        self, timestamp: datetime, event_type: str, message: str, payload: dict | None = None
    ) -> None:
        with self.sessions.begin() as db:
            db.add(
                ApplicationEventRow(
                    timestamp=timestamp,
                    event_type=event_type,
                    message=message,
                    payload=payload or {},
                )
            )

    def status(self) -> dict[str, object]:
        with self.sessions() as db:
            states = db.scalars(select(StrategyStateRow)).all()
            watchlist = db.scalar(
                select(WatchlistSessionRow).order_by(WatchlistSessionRow.id.desc())
            )
            signals = db.scalars(select(SignalRow).order_by(SignalRow.id.desc()).limit(20)).all()
            snapshots = db.scalars(
                select(MarketFeatureSnapshotRow).order_by(MarketFeatureSnapshotRow.id)
            ).all()
            latest_event = db.scalar(
                select(ApplicationEventRow).order_by(ApplicationEventRow.id.desc())
            )
            last_quotes = {row.symbol: row.timestamp for row in snapshots}
            if latest_event and latest_event.event_type == "market_data_heartbeat":
                connection = latest_event.payload.get("connection_state", "UNKNOWN")
            elif latest_event and latest_event.event_type == "shutdown":
                connection = "STOPPED"
            else:
                connection = "UNKNOWN"
            return {
                "watchlist": watchlist.symbols if watchlist else [],
                "states": {f"{row.symbol}:{row.direction}": row.state for row in states},
                "active_armed": [
                    row.symbol for row in states if row.state in {"ARMED", "WAITING_FOR_RETEST"}
                ],
                "recent_signals": [row.payload for row in signals],
                "last_quote_timestamp": last_quotes,
                "market_data_connection": connection,
                "connection_status_as_of": latest_event.timestamp if latest_event else None,
            }


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _aware_utc(parsed)
    return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _required_float(value)


def _required_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _option_report(
    option: ShadowOptionTradeRow | None, mark: ShadowOptionMarkRow | None
) -> dict[str, object] | None:
    if option is None:
        return None
    return {
        "symbol": option.option_symbol,
        "expiration": option.expiration,
        "strike": option.strike,
        "call_put": option.call_put,
        "quantity": option.quantity,
        "entry_fill": option.entry_fill,
        "entry_bid": option.entry_bid,
        "entry_ask": option.entry_ask,
        "entry_delta": option.entry_delta,
        "entry_gamma": option.entry_gamma,
        "entry_theta": option.entry_theta,
        "entry_iv": option.entry_iv,
        "entry_open_interest": option.entry_open_interest,
        "entry_volume": option.entry_volume,
        "execution_policy_version": option.selection_payload.get(
            "execution_policy_version", "OPTION_SHADOW_V1"
        ),
        "status": option.status,
        "exit_reason": option.exit_reason,
        "exit_fill": option.exit_fill,
        "realized_pnl": option.realized_pnl,
        "realized_return_pct": option.realized_return_pct,
        "latest_mark": mark.payload if mark else None,
    }
