import logging

from app.config.settings import StrategySettings
from app.runtime import LiveStrategyCoordinator
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction
from app.strategy.scenarios import scenario


def test_interesting_candidate_log_is_structured_and_throttled(caplog):
    settings = StrategySettings(candidate_log_cooldown_seconds=300)
    coordinator = LiveStrategyCoordinator(StrategyEngine(settings), settings)
    observation = scenario("triggered", Direction.BULLISH, "WATCH")[0]
    caplog.set_level(logging.INFO, logger="tradingpilot.strategy.candidates")

    coordinator._log_interesting_candidate(observation)
    coordinator._log_interesting_candidate(observation)

    records = [record for record in caplog.records if record.event == "interesting_candidate"]
    assert len(records) == 1
    record = records[0]
    assert record.symbol == "WATCH"
    assert record.direction == "BULLISH"
    assert record.check_score == 5
    assert record.check_total == 5
    assert record.missing_checks == ()
