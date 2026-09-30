from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import (
    ApplicationEventRow,
    MarketFeatureSnapshotRow,
    ShadowTradeOutcomeRow,
    ShadowTradeRow,
    SignalRow,
    StateTransitionRow,
    StrategyStateRow,
    WatchlistSessionRow,
)
from app.strategy.models import Signal, StateTransition


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
