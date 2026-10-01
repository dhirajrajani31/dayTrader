from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config.settings import StrategySettings
from app.runtime import LiveStrategyCoordinator
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction
from app.strategy.scenarios import scenario
from app.watchlist.context import WatchlistContext, load_watchlist_context
from app.watchlist.manager import WatchlistManager


def test_tomorrows_context_encodes_trigger_to_destination_paths():
    context = load_watchlist_context(Path("watchlist_context.json"))

    assert context is not None
    assert context.session_date == date(2026, 10, 1)
    assert context.plans["NVDA"].triggers[0].price == 228.79
    assert context.plans["NVDA"].triggers[0].targets == [232.0]
    assert context.plans["IOVA"].triggers[0].targets == [15.8, 17.0]
    assert context.plans["GOW"].triggers[0].targets == [5.0, 6.0]


def test_context_levels_only_apply_on_the_planned_session():
    context = WatchlistContext.model_validate(
        {
            "session_date": "2026-10-01",
            "plans": {
                "nvda": {
                    "note": "228.79 to 232",
                    "triggers": [{"price": 228.79, "targets": [232]}],
                }
            },
        }
    )
    timestamp = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago"))

    levels = context.levels_for("NVDA", timestamp, date(2026, 10, 1))

    assert len(levels) == 1
    assert levels[0].zone.contains(228.79)
    assert context.trigger_for_level("NVDA", levels[0]).targets == [232]
    assert context.levels_for("NVDA", timestamp, date(2026, 10, 2)) == []


def test_manual_bullish_level_can_be_approached_from_above():
    context = load_watchlist_context(Path("watchlist_context.json"))
    assert context is not None
    timestamp = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    levels = context.levels_for("NVDA", timestamp, date(2026, 10, 1))
    coordinator = LiveStrategyCoordinator(StrategyEngine(), StrategySettings(), context)

    selected = coordinator._nearby_manual_level(
        "NVDA", 229.40, Direction.BULLISH, levels
    )

    assert selected is not None
    assert selected.zone.contains(228.79)


@pytest.mark.asyncio
async def test_planned_destinations_become_shadow_trade_targets():
    engine = StrategyEngine()
    for index, observation in enumerate(scenario("triggered", Direction.BULLISH, "NVDA")):
        await engine.evaluate(
            observation.model_copy(update={"planned_targets": [232.0] if index == 0 else []})
        )

    signal = engine.signals[0]

    assert signal.target_1 == 232.0
    assert signal.target_2 is None
    assert signal.strategy_version == "0.2.0"


def test_new_symbols_are_added_without_removing_existing_symbols():
    symbols = WatchlistManager(Path("watchlist.txt")).load()

    assert {"MSTR", "COIN", "PLTR", "AAPL", "TSLA", "MU"} <= set(symbols)
    assert {"NVDA", "BE", "IOVA", "SDEV", "TGE", "GOW"} <= set(symbols)
