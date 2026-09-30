from app.options.models import OptionCandidate
from app.strategy.models import Direction, SetupState, StateTransition


def _metric(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_transition(transition: StateTransition, option: OptionCandidate | None = None) -> str:
    obs = transition.observation
    icons = {
        SetupState.ARMED: "🟡",
        SetupState.TRIGGERED: "🔴",
        SetupState.INVALIDATED: "⚪",
        SetupState.EXTENDED: "🟠",
        SetupState.WAITING_FOR_RETEST: "🔵",
    }
    state_name = transition.to_state.value.replace("_", " ")
    title = f"{icons.get(transition.to_state, '•')} {obs.symbol} {obs.direction.value} {state_name}"
    lines = [title, "", f"Price: ${obs.price:.2f}"]
    if obs.level:
        lines.append(f"Level: ${obs.level.midpoint:.2f} {obs.level.type.lower().replace('_', ' ')}")
    if obs.vwap is not None:
        lines.append(f"VWAP: ${obs.vwap:.2f}")
    if obs.relative_volume is not None:
        lines.append(f"RVOL: {obs.relative_volume:.1f}x")
    lines.extend(["", transition.reason])
    if transition.to_state == SetupState.ARMED:
        action = (
            "breakout + retest + continuation"
            if obs.direction == Direction.BULLISH
            else "breakdown + failed reclaim + continuation"
        )
        lines.extend(["", f"Waiting for: {action}", "DO NOT ENTER YET"])
    elif transition.to_state == SetupState.TRIGGERED:
        lines.append("")
        if option:
            lines.extend(
                [
                    f"Option shadow: {option.symbol}",
                    (
                        f"{option.expiration.date().isoformat()} | ${option.strike:g} "
                        f"{option.call_put.upper()} | {option.days_to_expiration} DTE"
                    ),
                    (
                        f"Bid/ask: ${option.bid:.2f} / ${option.ask:.2f} | "
                        f"simulated buy: ${option.simulated_entry_fill:.2f}"
                    ),
                    (
                        f"Delta: {_metric(option.delta)} | Gamma: {_metric(option.gamma)} | "
                        f"Theta: {_metric(option.theta)} | "
                        f"IV: {_percent(option.iv)}"
                    ),
                    (
                        f"Spread: {option.spread_pct:.1%} | OI: {option.open_interest or 0:,} | "
                        f"Volume: {option.volume or 0:,}"
                    ),
                ]
            )
        else:
            lines.append("Option selection unavailable")
        lines.extend(["", "SHADOW MODE — NO ORDER SENT"])
    elif transition.to_state == SetupState.EXTENDED:
        lines.extend(["", "DO NOT CHASE."])
    return "\n".join(lines)
