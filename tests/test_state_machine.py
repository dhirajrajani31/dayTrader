import pytest

from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction, SetupState
from app.strategy.scenarios import scenario


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "outcome", "expected"),
    [
        (Direction.BULLISH, "triggered", SetupState.TRIGGERED),
        (Direction.BULLISH, "invalidated", SetupState.INVALIDATED),
        (Direction.BULLISH, "extended", SetupState.EXTENDED),
        (Direction.BEARISH, "triggered", SetupState.TRIGGERED),
        (Direction.BEARISH, "invalidated", SetupState.INVALIDATED),
    ],
)
async def test_required_scenarios(direction, outcome, expected):
    engine = StrategyEngine()
    for observation in scenario(outcome, direction):
        await engine.evaluate(observation)
    machine = next(iter(engine.machines.values()))
    assert machine.state == expected
    assert [transition.to_state for transition in machine.transitions][:2] == [
        SetupState.ARMED,
        SetupState.WAITING_FOR_RETEST,
    ]


@pytest.mark.asyncio
async def test_transition_reasons_and_strategy_version_are_preserved():
    engine = StrategyEngine()
    transitions = []
    for observation in scenario("triggered", Direction.BULLISH):
        transition = await engine.evaluate(observation)
        if transition:
            transitions.append(transition)
    assert all(item.reason for item in transitions)
    assert all(item.strategy_name == "MR_INVESTR_BASELINE" for item in transitions)
    assert all(item.strategy_version == "0.1.0" for item in transitions)
    assert engine.signals[0].strategy_version == "0.1.0"
