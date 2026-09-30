from app.strategy.models import BenchmarkContext, DataQuality, Direction, FeatureSnapshot


def build_benchmark_context(snapshot: FeatureSnapshot) -> BenchmarkContext:
    momentum = snapshot.momentum.value
    direction = (
        Direction.BULLISH
        if momentum is not None and momentum > 0
        else (Direction.BEARISH if momentum is not None and momentum < 0 else None)
    )
    distance = snapshot.distance_from_vwap.value
    return BenchmarkContext(
        symbol=snapshot.symbol,
        direction=direction,
        above_vwap=None if distance is None else distance >= 0,
        momentum=momentum,
        quality=snapshot.price.quality if momentum is not None else DataQuality.PARTIAL,
    )
