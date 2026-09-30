import logging

from app.logging_config import configure_logging


def test_http_clients_do_not_log_sensitive_request_urls_at_info(tmp_path):
    configure_logging(tmp_path / "pilot.log")
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
