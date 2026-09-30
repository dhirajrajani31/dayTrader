from datetime import timedelta

from app.shadow.tracker import ShadowTracker
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction
from app.strategy.scenarios import scenario


async def test_shadow_tracker_records_checkpoints_excursions_and_targets():
    engine = StrategyEngine()
    for observation in scenario("triggered", Direction.BULLISH):
        await engine.evaluate(observation)
    signal = engine.signals[0]
    tracker = ShadowTracker()
    trade = tracker.open(signal, {"vwap": 99.5})
    tracker.update(signal.symbol, signal.timestamp + timedelta(minutes=5), signal.price + 1)
    tracker.update(signal.symbol, signal.timestamp + timedelta(minutes=15), signal.price - 1)
    tracker.update(signal.symbol, signal.timestamp + timedelta(minutes=30), signal.target_2)
    tracker.update(signal.symbol, signal.timestamp + timedelta(minutes=60), signal.price)
    assert set(trade.prices_after) == {"5m", "15m", "30m", "60m"}
    assert trade.maximum_favorable_excursion > 0
    assert trade.maximum_adverse_excursion > 0
    assert trade.target_1_hit and trade.target_2_hit
    assert trade.estimated_r_multiple is not None
