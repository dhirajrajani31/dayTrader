from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import StrategySettings
from app.strategy.models import Direction, StrategyObservation


@dataclass(frozen=True)
class RuleResult:
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateAssessment:
    interesting: bool
    score: int
    total: int
    passed: tuple[str, ...]
    missing: tuple[str, ...]


def arm_conditions(obs: StrategyObservation, cfg: StrategySettings) -> dict[str, bool]:
    if obs.level is None:
        return {
            "important level approached": False,
            "abnormal activity": False,
            "directional relative strength": False,
            "VWAP/opening-range context": False,
            "adequate room": False,
        }
    level = obs.level.midpoint
    near = abs(obs.price - level) / level <= cfg.approach_distance_pct
    activity = (
        obs.relative_volume is not None and obs.relative_volume >= cfg.minimum_relative_volume
    )
    directional_rs = obs.relative_strength is not None and (
        (obs.direction == Direction.BULLISH and obs.relative_strength > 0)
        or (obs.direction == Direction.BEARISH and obs.relative_strength < 0)
    )
    vwap_context = obs.vwap is not None and (
        (obs.direction == Direction.BULLISH and obs.price >= obs.vwap)
        or (obs.direction == Direction.BEARISH and obs.price <= obs.vwap)
    )
    context = vwap_context or obs.opening_range_context
    room = obs.reward_risk is not None and obs.reward_risk >= cfg.minimum_reward_risk
    return {
        "important level approached": near,
        "abnormal activity": activity,
        "directional relative strength": directional_rs,
        "VWAP/opening-range context": context,
        "adequate room": room,
    }


def candidate_assessment(obs: StrategyObservation, cfg: StrategySettings) -> CandidateAssessment:
    checks = arm_conditions(obs, cfg)
    passed = tuple(name for name, result in checks.items() if result)
    missing = tuple(name for name, result in checks.items() if not result)
    near_level = checks["important level approached"]
    return CandidateAssessment(
        interesting=near_level and len(passed) >= cfg.candidate_log_minimum_checks,
        score=len(passed),
        total=len(checks),
        passed=passed,
        missing=missing,
    )


def arm_check(obs: StrategyObservation, cfg: StrategySettings) -> RuleResult:
    if obs.level is None:
        return RuleResult(False, ("no meaningful technical level",))
    checks = arm_conditions(obs, cfg)
    return RuleResult(
        all(checks.values()), tuple(name for name, passed in checks.items() if passed)
    )


def breakout_check(obs: StrategyObservation, cfg: StrategySettings) -> RuleResult:
    if obs.level is None:
        return RuleResult(False, ("no level",))
    buffer = obs.level.midpoint * cfg.breakout_buffer_pct
    broken = (
        obs.price > obs.level.zone.high + buffer
        if obs.direction == Direction.BULLISH
        else obs.price < obs.level.zone.low - buffer
    )
    return RuleResult(
        broken and obs.volume_confirmed,
        ("level broken", "volume confirmed") if broken and obs.volume_confirmed else (),
    )


def retest_holds(obs: StrategyObservation, cfg: StrategySettings) -> RuleResult:
    if obs.level is None:
        return RuleResult(False, ("no level",))
    tolerance = obs.level.midpoint * cfg.retest_tolerance_pct
    confirmation = obs.level.midpoint * cfg.confirmation_distance_pct
    if obs.direction == Direction.BULLISH:
        touched = obs.candle_low <= obs.level.zone.high + tolerance
        held = obs.price > obs.level.zone.high + confirmation and obs.price > obs.candle_open
    else:
        touched = obs.candle_high >= obs.level.zone.low - tolerance
        held = obs.price < obs.level.zone.low - confirmation and obs.price < obs.candle_open
    checks = (touched, held, obs.volume_confirmed)
    reasons = ("retest occurred", "structure held", "continuation and volume confirmed")
    return RuleResult(
        all(checks), tuple(reason for reason, passed in zip(reasons, checks, strict=True) if passed)
    )


def invalidated(obs: StrategyObservation, cfg: StrategySettings) -> bool:
    if obs.level is None:
        return False
    buffer = obs.level.midpoint * cfg.invalidation_buffer_pct
    return (
        obs.price < obs.level.zone.low - buffer
        if obs.direction == Direction.BULLISH
        else obs.price > obs.level.zone.high + buffer
    )


def extended(obs: StrategyObservation, cfg: StrategySettings) -> bool:
    if obs.level is None:
        return False
    distance = (
        obs.price / obs.level.zone.high - 1
        if obs.direction == Direction.BULLISH
        else obs.level.zone.low / obs.price - 1
    )
    return distance >= cfg.extension_threshold_pct
