from app.options.models import OptionCandidate
from app.strategy.models import Direction, SetupState, StateTransition


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
        lines.extend(
            [
                "",
                f"Option: {option.symbol}" if option else "Option selection unavailable",
                "",
                "SHADOW MODE — NO ORDER SENT",
            ]
        )
    elif transition.to_state == SetupState.EXTENDED:
        lines.extend(["", "DO NOT CHASE."])
    return "\n".join(lines)
