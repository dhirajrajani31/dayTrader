from collections.abc import Sequence

from app.config.settings import OptionSettings
from app.options.models import OptionCandidate
from app.strategy.models import Direction


def select_option(
    candidates: Sequence[OptionCandidate],
    underlying: float,
    direction: Direction,
    settings: OptionSettings | None = None,
) -> OptionCandidate | None:
    cfg = settings or OptionSettings()
    desired = "CALL" if direction == Direction.BULLISH else "PUT"
    viable: list[OptionCandidate] = []
    for item in candidates:
        if (
            item.call_put.upper() != desired
            or not cfg.min_days_to_expiration
            <= item.days_to_expiration
            <= cfg.max_days_to_expiration
            or item.mid is None
            or item.mid <= 0
            or item.spread is None
            or item.delta is None
        ):
            continue
        if item.spread / item.mid > cfg.maximum_spread_pct:
            continue
        if not cfg.minimum_abs_delta <= abs(item.delta) <= cfg.maximum_abs_delta:
            continue
        if direction == Direction.BULLISH and item.strike > underlying * 1.02:
            continue
        if direction == Direction.BEARISH and item.strike < underlying * 0.98:
            continue
        viable.append(item)
    return min(
        viable,
        key=lambda item: (
            item.days_to_expiration,
            abs(abs(item.delta or cfg.target_abs_delta) - cfg.target_abs_delta),
            item.spread_pct or float("inf"),
            -(item.open_interest or 0),
            -(item.volume or 0),
            item.expiration,
            abs(item.strike - underlying),
        ),
        default=None,
    )
