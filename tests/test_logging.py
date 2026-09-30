import json
import logging

from app.logging_config import JsonFormatter, configure_logging


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
