from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import OptionSettings
from app.market_data.models import CandleEvent, QuoteEvent
from app.options.models import OptionCandidate
from app.options.selector import select_option
from app.shadow.option_tracker import OptionShadowTracker
from app.shadow.outcome_engine import outcome_summary
from app.shadow.report import format_trade_report
from app.shadow.tracker import ShadowTracker
from app.storage.database import create_database
from app.storage.repository import Repository
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction
from app.strategy.scenarios import scenario


def candidate(**updates) -> OptionCandidate:
    values = {
        "symbol": "TEST  261002P00101000",
        "streamer_symbol": ".TEST261002P101",
        "underlying_symbol": "TEST",
        "expiration": datetime(2026, 10, 2, 20, tzinfo=UTC),
        "days_to_expiration": 2,
        "strike": 101.0,
        "call_put": "PUT",
        "quote_timestamp": datetime(2026, 9, 30, 17, 0, tzinfo=UTC),
        "bid": 1.0,
        "ask": 1.1,
        "delta": -0.62,
        "gamma": 0.04,
        "theta": -0.08,
        "iv": 0.55,
        "open_interest": 500,
        "volume": 100,
    }
    values.update(updates)
    return OptionCandidate(**values)


def test_selector_uses_real_quote_greeks_and_liquidity_rules():
    good = candidate()
    wide = candidate(symbol="WIDE", bid=0.5, ask=1.5)
    lottery = candidate(symbol="LOTTERY", strike=90, delta=-0.2)
    call = candidate(symbol="CALL", call_put="CALL", delta=0.62)

    selected = select_option([wide, lottery, call, good], 100, Direction.BEARISH, OptionSettings())

    assert selected == good
    assert selected.simulated_entry_fill == 1.1


@pytest.mark.asyncio
async def test_option_shadow_uses_ask_entry_bid_exit_and_underlying_target():
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BEARISH, "TEST"):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    underlying_tracker = ShadowTracker()
    underlying = underlying_tracker.open(signal)
    option_tracker = OptionShadowTracker(OptionSettings())
    option = option_tracker.open(1, signal, candidate())
    candle = CandleEvent(
        symbol="TEST",
        timestamp=signal.timestamp + timedelta(minutes=5),
        interval_seconds=60,
        open=signal.price,
        high=signal.price + 0.1,
        low=signal.target_1 - 0.01,
        close=signal.target_1,
        volume=1000,
    )
    underlying_tracker.update_candle(candle)
    quote = QuoteEvent(
        symbol=option.option_symbol,
        timestamp=candle.timestamp + timedelta(seconds=60),
        bid=1.5,
        ask=1.6,
    )

    outcome = option_tracker.update(candle, quote, underlying)

    assert outcome is not None
    assert option.status == "CLOSED"
    assert option.exit_reason == "UNDERLYING_TARGET_1"
    assert option.entry_fill == 1.1
    assert option.exit_fill == 1.5
    assert option.realized_pnl == pytest.approx(40)
    assert option.realized_return_pct == pytest.approx(40 / 110)
    assert option.net_realized_pnl == pytest.approx(36.7)


@pytest.mark.asyncio
async def test_same_candle_target_and_stop_uses_conservative_stop_exit():
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BEARISH, "TEST"):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    underlying_tracker = ShadowTracker()
    underlying = underlying_tracker.open(signal)
    option_tracker = OptionShadowTracker(OptionSettings())
    option = option_tracker.open(1, signal, candidate())
    candle = CandleEvent(
        symbol="TEST",
        timestamp=signal.timestamp + timedelta(minutes=1),
        interval_seconds=60,
        open=signal.price,
        high=signal.invalidation + 0.01,
        low=signal.target_1 - 0.01,
        close=signal.price,
        volume=1000,
    )
    underlying_tracker.update_candle(candle)
    quote = QuoteEvent(
        symbol=option.option_symbol,
        timestamp=candle.timestamp + timedelta(seconds=60),
        bid=0.8,
        ask=0.9,
    )

    option_tracker.update(candle, quote, underlying)

    assert underlying.stop_hit and underlying.target_1_hit
    assert option.exit_reason == "UNDERLYING_INVALIDATION"
    assert option.realized_pnl == pytest.approx(-30)
    assert option.net_realized_pnl == pytest.approx(-33.3)


@pytest.mark.asyncio
async def test_option_trade_and_marks_survive_restart_and_render_report(tmp_path):
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BEARISH, "TEST"):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    _, sessions = create_database(f"sqlite:///{tmp_path / 'options.db'}")
    repository = Repository(sessions)
    shadow_trade_id = repository.save_shadow_trade(signal, {"source": "test"})
    underlying_tracker = ShadowTracker()
    underlying = underlying_tracker.open(signal)
    settings = OptionSettings(execution_policy_version="OPTION_SHADOW_TEST")
    option_tracker = OptionShadowTracker(settings)
    option = option_tracker.open(shadow_trade_id, signal, candidate())
    option.option_trade_id = repository.save_option_trade(
        option,
        {
            **candidate().model_dump(mode="json"),
            "execution_policy_version": settings.execution_policy_version,
        },
    )
    candle = CandleEvent(
        symbol="TEST",
        timestamp=signal.timestamp + timedelta(minutes=5),
        interval_seconds=60,
        open=signal.price,
        high=signal.price + 0.1,
        low=signal.price - 0.1,
        close=signal.price,
        volume=1000,
    )
    underlying_tracker.update_candle(candle)
    quote = QuoteEvent(
        symbol=option.option_symbol,
        timestamp=candle.timestamp + timedelta(seconds=60),
        bid=1.2,
        ask=1.3,
    )
    mark = option_tracker.update(candle, quote, underlying)
    assert mark is not None and option.option_trade_id is not None
    repository.save_option_mark(option.option_trade_id, quote.timestamp, mark)
    underlying.tracking_complete = True
    repository.save_outcome(shadow_trade_id, quote.timestamp, outcome_summary(underlying))

    restored = repository.load_active_option_trades()
    restored_underlying = repository.load_active_shadow_trades(
        signal.timestamp + timedelta(days=1)
    )
    report_rows = repository.recent_trade_report()
    rendered = format_trade_report(report_rows)

    assert len(restored) == 1
    assert len(restored_underlying) == 1
    assert restored[0].latest_at == quote.timestamp
    assert restored[0].latest_bid == 1.2
    assert restored[0].execution_policy_version == "OPTION_SHADOW_TEST"
    assert "TEST  261002P00101000" in rendered
    assert "net P&L $6.70" in rendered
    assert "Execution policy: OPTION_SHADOW_TEST" in rendered


@pytest.mark.asyncio
async def test_missing_exit_quote_remains_pending_and_later_closes_as_delayed():
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BEARISH, "TEST"):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    underlying_tracker = ShadowTracker()
    underlying = underlying_tracker.open(signal)
    option_tracker = OptionShadowTracker(OptionSettings())
    option = option_tracker.open(1, signal, candidate())
    stop_candle = CandleEvent(
        symbol="TEST",
        timestamp=signal.timestamp + timedelta(minutes=1),
        interval_seconds=60,
        open=signal.price,
        high=signal.invalidation + 0.01,
        low=signal.price,
        close=signal.price,
        volume=1000,
    )
    underlying_tracker.update_candle(stop_candle)

    missed = option_tracker.update(stop_candle, None, underlying)

    assert missed is not None
    assert missed["execution_event"]["status"] == "MISSED"
    assert option.status == "OPEN"
    assert option.pending_exit_reason == "UNDERLYING_INVALIDATION"

    next_candle = stop_candle.model_copy(
        update={"timestamp": stop_candle.timestamp + timedelta(minutes=1)}
    )
    underlying_tracker.update_candle(next_candle)
    delayed_quote = QuoteEvent(
        symbol=option.option_symbol,
        timestamp=next_candle.timestamp + timedelta(seconds=60),
        bid=0.7,
        ask=0.8,
    )
    delayed = option_tracker.update(next_candle, delayed_quote, underlying)

    assert delayed is not None
    assert delayed["execution_event"]["status"] == "DELAYED"
    assert option.status == "CLOSED"
    assert option.exit_reason == "UNDERLYING_INVALIDATION"
    assert option.exit_delay_seconds == 60


@pytest.mark.asyncio
async def test_pending_exit_and_execution_failure_survive_database_restart(tmp_path):
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BEARISH, "TEST"):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    _, sessions = create_database(f"sqlite:///{tmp_path / 'pending.db'}")
    repository = Repository(sessions)
    shadow_trade_id = repository.save_shadow_trade(signal, {"source": "test"})
    underlying_tracker = ShadowTracker()
    underlying = underlying_tracker.open(signal)
    option_tracker = OptionShadowTracker(OptionSettings())
    option = option_tracker.open(shadow_trade_id, signal, candidate())
    option.option_trade_id = repository.save_option_trade(
        option, candidate().model_dump(mode="json")
    )
    candle = CandleEvent(
        symbol="TEST",
        timestamp=signal.timestamp + timedelta(minutes=1),
        interval_seconds=60,
        open=signal.price,
        high=signal.invalidation + 0.01,
        low=signal.price,
        close=signal.price,
        volume=1000,
    )
    underlying_tracker.update_candle(candle)
    payload = option_tracker.update(candle, None, underlying)
    assert payload is not None and option.option_trade_id is not None
    mark_at = candle.timestamp + timedelta(seconds=60)
    repository.save_option_mark(option.option_trade_id, mark_at, payload)
    event = payload["execution_event"]
    repository.save_execution_event(
        symbol="TEST",
        timestamp=mark_at,
        stage=event["stage"],
        status=event["status"],
        reason_code=event["reason_code"],
        shadow_trade_id=shadow_trade_id,
        shadow_option_trade_id=option.option_trade_id,
        intended_at=datetime.fromisoformat(event["intended_at"]),
    )

    restored = repository.load_active_option_trades()
    dataset = repository.evaluation_dataset()

    assert len(restored) == 1
    assert restored[0].pending_exit_reason == "UNDERLYING_INVALIDATION"
    assert restored[0].pending_exit_at == mark_at
    assert dataset["execution_events"][0]["reason_code"] == "NO_OPTION_QUOTE"
