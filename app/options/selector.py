from collections.abc import Sequence

from app.options.models import OptionCandidate
from app.strategy.models import Direction


def select_option(
    candidates: Sequence[OptionCandidate],
    underlying: float,
    direction: Direction,
    max_spread_pct: float = 0.20,
) -> OptionCandidate | None:
    desired = "CALL" if direction == Direction.BULLISH else "PUT"
    viable: list[OptionCandidate] = []
    for item in candidates:
        if (
            item.call_put.upper() != desired
            or item.mid is None
            or item.mid <= 0
            or item.spread is None
        ):
            continue
        if item.spread / item.mid > max_spread_pct:
            continue
        if item.delta is not None and not 0.55 <= abs(item.delta) <= 0.70:
            continue
        if direction == Direction.BULLISH and item.strike > underlying * 1.02:
            continue
        if direction == Direction.BEARISH and item.strike < underlying * 0.98:
            continue
        viable.append(item)
    return min(
        viable,
        key=lambda item: (
            item.expiration,
            abs(abs(item.delta or 0.6) - 0.625),
            abs(item.strike - underlying),
        ),
        default=None,
    )
