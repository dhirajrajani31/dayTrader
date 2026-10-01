import json
import logging

from app.logging_config import CandidateFormatter, JsonFormatter, configure_logging


def test_http_clients_do_not_log_sensitive_request_urls_at_info(tmp_path):
    configure_logging(tmp_path / "pilot.log")
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_candidate_fields_are_included_in_json_log():
    record = logging.LogRecord(
        "tradingpilot.strategy.candidates",
        logging.INFO,
        __file__,
        1,
        "interesting_candidate",
        (),
        None,
    )
    record.event = "interesting_candidate"
    record.symbol = "MSTR"
    record.direction = "BULLISH"
    record.check_score = 4
    record.check_total = 5
    record.passed_checks = ("important level approached", "abnormal activity")
    record.missing_checks = ("adequate room",)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["symbol"] == "MSTR"
    assert payload["direction"] == "BULLISH"
    assert payload["check_score"] == 4
    assert payload["missing_checks"] == ["adequate room"]


def test_candidate_formatter_is_readable_for_an_operator():
    record = logging.LogRecord(
        "tradingpilot.strategy.candidates",
        logging.INFO,
        __file__,
        1,
        "interesting_candidate",
        (),
        None,
    )
    record.event = "interesting_candidate"
    record.symbol = "COIN"
    record.direction = "BEARISH"
    record.price = 187.5
    record.level_type = "SWING_LOW"
    record.level_price = 187.34
    record.distance_pct = 0.000854
    record.relative_volume = 1.3014
    record.relative_strength = -0.04684
    record.reward_risk = 0.3179
    record.check_score = 3
    record.check_total = 5
    record.passed_checks = (
        "important level approached",
        "directional relative strength",
        "VWAP/opening-range context",
    )
    record.missing_checks = ("abnormal activity", "adequate room")

    message = CandidateFormatter().format(record)

    assert "COIN | Bearish watch only | 3/5 checks" in message
    assert "Price $187.50 | Swing Low $187.34 | 0.09% from level" in message
    assert "RVOL 1.30x | Relative strength -4.68% | Reward/risk 0.32" in message
    assert "Confirmed: near the level; directional relative strength" in message
    assert "Still missing: unusual volume; adequate reward/risk" in message
    assert "Status: WATCH ONLY - NOT ARMED" in message


def test_configured_candidate_feed_is_separate_from_json_log(tmp_path):
    json_path = tmp_path / "pilot.log"
    configure_logging(json_path)
    logger = logging.getLogger("tradingpilot.strategy.candidates")
    logger.info(
        "interesting_candidate",
        extra={
            "event": "interesting_candidate",
            "symbol": "AAPL",
            "direction": "BULLISH",
            "price": 200.0,
            "level_type": "PREVIOUS_DAY_HIGH",
            "level_price": 199.8,
            "distance_pct": 0.001,
            "relative_volume": 1.8,
            "relative_strength": 0.012,
            "reward_risk": 2.0,
            "check_score": 5,
            "check_total": 5,
            "passed_checks": ("important level approached",),
            "missing_checks": (),
        },
    )
    for handler in logger.handlers:
        handler.flush()

    assert "AAPL | Bullish candidate | 5/5 checks" in (tmp_path / "candidates.log").read_text(
        encoding="utf-8"
    )
    assert "interesting_candidate" not in json_path.read_text(encoding="utf-8")
