import pytest

from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction, SetupState
from app.strategy.scenarios import scenario


@pytest.mark.asyncio
async def test_simple_vwap_cross_without_confirmations_does_not_trigger():
    observation = scenario("triggered", Direction.BULLISH)[0].model_copy(
        update={"level": None, "relative_volume": 1.0, "relative_strength": None}
    )
    engine = StrategyEngine()
    await engine.evaluate(observation)
    assert next(iter(engine.machines.values())).state == SetupState.WATCHING
    assert not engine.signals


@pytest.mark.asyncio
async def test_support_touch_without_confirmation_does_not_trigger():
    observation = scenario("triggered", Direction.BULLISH)[0].model_copy(
        update={"volume_confirmed": False, "relative_volume": 1.0}
    )
    engine = StrategyEngine()
    await engine.evaluate(observation)
    assert next(iter(engine.machines.values())).state == SetupState.WATCHING


@pytest.mark.asyncio
async def test_breakout_without_retest_stays_waiting():
    engine = StrategyEngine()
    observations = scenario("triggered", Direction.BULLISH)
    for observation in observations[:2]:
        await engine.evaluate(observation)
    assert next(iter(engine.machines.values())).state == SetupState.WAITING_FOR_RETEST
    assert not engine.signals
