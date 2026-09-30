from app.shadow.tracker import ShadowTradeRecord


def outcome_summary(trade: ShadowTradeRecord) -> dict[str, object]:
    return {
        "prices_after": trade.prices_after,
        "mfe": trade.maximum_favorable_excursion,
        "mae": trade.maximum_adverse_excursion,
        "stop_hit": trade.stop_hit,
        "stop_hit_time": trade.stop_hit_time.isoformat() if trade.stop_hit_time else None,
        "target_1_hit": trade.target_1_hit,
        "target_2_hit": trade.target_2_hit,
        "time_to_target_1_seconds": trade.time_to_target_1_seconds,
        "time_to_target_2_seconds": trade.time_to_target_2_seconds,
        "estimated_r_multiple": trade.estimated_r_multiple,
        "tracking_complete": trade.tracking_complete,
    }
