import pytest

from app.config.settings import StrategySettings
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction, SetupState
from app.strategy.rules import candidate_assessment
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


def test_interesting_candidate_requires_direction_and_context_alignment():
    settings = StrategySettings()
    observation = scenario("triggered", Direction.BULLISH)[0].model_copy(
        update={"relative_volume": 1.0, "reward_risk": None}
    )
    assessment = candidate_assessment(observation, settings)
    assert assessment.interesting
    assert assessment.score == 3
    assert "important level approached" in assessment.passed
    assert "abnormal activity" in assessment.missing

    contradictory = scenario("triggered", Direction.BULLISH)[0].model_copy(
        update={"direction": Direction.BEARISH, "opening_range_context": False}
    )
    contradictory_assessment = candidate_assessment(contradictory, settings)
    assert contradictory_assessment.score == 3
    assert "directional relative strength" in contradictory_assessment.missing
    assert "VWAP/opening-range context" in contradictory_assessment.missing
    assert not contradictory_assessment.interesting

    far_from_level = observation.model_copy(update={"price": 105.0, "reward_risk": 2.0})
    far_assessment = candidate_assessment(far_from_level, settings)
    assert far_assessment.score >= 3
    assert not far_assessment.interesting
