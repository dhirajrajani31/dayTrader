from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.config.settings import StrategySettings
from app.strategy.models import Direction, SetupState, Signal, StateTransition, StrategyObservation
from app.strategy.state_machine import StrategyStateMachine

TransitionHandler = Callable[[StateTransition], Awaitable[None]]


class StrategyEngine:
    def __init__(
        self,
        settings: StrategySettings | None = None,
        on_transition: TransitionHandler | None = None,
    ):
        self.settings = settings or StrategySettings()
        self.on_transition = on_transition
        self.machines: dict[tuple[str, Direction], StrategyStateMachine] = {}
        self.signals: list[Signal] = []

    async def evaluate(self, observation: StrategyObservation) -> StateTransition | None:
        key = (observation.symbol, observation.direction)
        machine = self.machines.setdefault(
            key, StrategyStateMachine(observation.symbol, observation.direction, self.settings)
        )
        transition = machine.evaluate(observation)
        if transition is None:
            return None
        if transition.to_state == SetupState.TRIGGERED:
            self.signals.append(self._signal(transition))
        if self.on_transition:
            await self.on_transition(transition)
        return transition

    def _signal(self, transition: StateTransition) -> Signal:
        obs = transition.observation
        midpoint = obs.level.midpoint if obs.level else obs.price
        buffer = midpoint * self.settings.invalidation_buffer_pct
        invalidation = (
            (obs.level.zone.low if obs.level else midpoint) - buffer
            if obs.direction == Direction.BULLISH
            else (obs.level.zone.high if obs.level else midpoint) + buffer
        )
        risk = abs(obs.price - invalidation)
        sign = 1 if obs.direction == Direction.BULLISH else -1
        if obs.planned_targets:
            return Signal(
                symbol=obs.symbol,
                timestamp=obs.timestamp,
                direction=obs.direction,
                state=SetupState.TRIGGERED,
                strategy_name=self.settings.name,
                strategy_version=self.settings.version,
                price=obs.price,
                invalidation=invalidation,
                target_1=obs.planned_targets[0],
                target_2=(obs.planned_targets[1] if len(obs.planned_targets) > 1 else None),
                reasons=transition.reason.split("; "),
            )
        target_1 = obs.price + sign * risk * self.settings.minimum_reward_risk
        two_r = obs.price + sign * risk * 2
        next_level_is_beyond_target_1 = (
            obs.next_level is not None and sign * (obs.next_level - target_1) > 0
        )
        target_2 = obs.next_level if next_level_is_beyond_target_1 else two_r
        return Signal(
            symbol=obs.symbol,
            timestamp=obs.timestamp,
            direction=obs.direction,
            state=SetupState.TRIGGERED,
            strategy_name=self.settings.name,
            strategy_version=self.settings.version,
            price=obs.price,
            invalidation=invalidation,
            target_1=target_1,
            target_2=target_2,
            reasons=transition.reason.split("; "),
        )

    def states(self) -> dict[str, str]:
        return {
            f"{symbol}:{direction.value}": machine.state.value
            for (symbol, direction), machine in self.machines.items()
        }

    def restore(self, transitions: list[StateTransition]) -> None:
        """Restore today's latest deterministic state without replaying alerts."""
        for transition in transitions:
            key = (transition.symbol, transition.direction)
            machine = StrategyStateMachine(transition.symbol, transition.direction, self.settings)
            machine.state = transition.to_state
            if transition.to_state != SetupState.WATCHING:
                machine.active_level = transition.observation.level
                machine.active_planned_targets = list(transition.observation.planned_targets)
            machine.transitions.append(transition)
            self.machines[key] = machine
