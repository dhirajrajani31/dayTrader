from __future__ import annotations

from app.config.settings import StrategySettings
from app.strategy.models import Direction, Level, SetupState, StateTransition, StrategyObservation
from app.strategy.rules import arm_check, breakout_check, extended, invalidated, retest_holds


class StrategyStateMachine:
    def __init__(self, symbol: str, direction: Direction, settings: StrategySettings | None = None):
        self.symbol = symbol
        self.direction = direction
        self.settings = settings or StrategySettings()
        self.state = SetupState.WATCHING
        self.active_level: Level | None = None
        self.active_planned_targets: list[float] = []
        self.transitions: list[StateTransition] = []

    def evaluate(self, observation: StrategyObservation) -> StateTransition | None:
        if observation.symbol != self.symbol or observation.direction != self.direction:
            raise ValueError("observation does not match state machine")
        if self.state != SetupState.WATCHING and self.active_level is not None:
            observation = observation.model_copy(
                update={
                    "level": self.active_level,
                    "planned_targets": self.active_planned_targets,
                }
            )
        target: SetupState | None = None
        reason = ""
        if self.state == SetupState.WATCHING:
            result = arm_check(observation, self.settings)
            if result.passed:
                target = SetupState.ARMED
                reason = "; ".join(result.reasons)
        elif self.state == SetupState.ARMED:
            result = breakout_check(observation, self.settings)
            if result.passed:
                target = SetupState.WAITING_FOR_RETEST
                level_name = observation.level.type if observation.level else "level"
                reason = f"{level_name} broken with elevated volume; waiting for retest"
            elif invalidated(observation, self.settings):
                target = SetupState.INVALIDATED
                reason = "setup structure failed before breakout"
        elif self.state == SetupState.WAITING_FOR_RETEST:
            if invalidated(observation, self.settings):
                target = SetupState.INVALIDATED
                reason = "retest lost breakout structure"
            elif extended(observation, self.settings):
                target = SetupState.EXTENDED
                reason = "move exceeded extension threshold before a valid retest; DO NOT CHASE"
            else:
                result = retest_holds(observation, self.settings)
                if result.passed:
                    target = SetupState.TRIGGERED
                    reason = "; ".join(result.reasons)
        if target is None:
            return None
        if target == SetupState.ARMED:
            self.active_level = observation.level
            self.active_planned_targets = list(observation.planned_targets)
        transition = StateTransition(
            symbol=self.symbol,
            timestamp=observation.timestamp,
            from_state=self.state,
            to_state=target,
            direction=self.direction,
            reason=reason,
            strategy_name=self.settings.name,
            strategy_version=self.settings.version,
            observation=observation,
        )
        self.state = target
        self.transitions.append(transition)
        return transition
