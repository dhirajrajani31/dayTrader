from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "event",
            "symbol",
            "direction",
            "state",
            "connection_state",
            "price",
            "level_type",
            "level_price",
            "distance_pct",
            "relative_volume",
            "relative_strength",
            "reward_risk",
            "check_score",
            "check_total",
            "passed_checks",
            "missing_checks",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ExcludeCandidateFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) != "interesting_candidate"


class CandidateFormatter(logging.Formatter):
    labels = {
        "important level approached": "near the level",
        "abnormal activity": "unusual volume",
        "directional relative strength": "directional relative strength",
        "VWAP/opening-range context": "VWAP/opening-range alignment",
        "adequate room": "adequate reward/risk",
    }

    @staticmethod
    def _number(record: logging.LogRecord, field: str, spec: str, suffix: str = "") -> str:
        value = getattr(record, field, None)
        return "n/a" if value is None else f"{float(value):{spec}}{suffix}"

    def _checks(self, record: logging.LogRecord, field: str) -> str:
        values = getattr(record, field, ())
        return "; ".join(self.labels.get(value, value) for value in values) or "none"

    def format(self, record: logging.LogRecord) -> str:
        direction = str(getattr(record, "direction", "")).title()
        level_type = str(getattr(record, "level_type", "level")).replace("_", " ").title()
        score = getattr(record, "check_score", "?")
        total = getattr(record, "check_total", "?")
        lines = [
            (
                f"[{self.formatTime(record, '%Y-%m-%d %H:%M:%S %z')}] "
                f"{getattr(record, 'symbol', '?')} | {direction} candidate | {score}/{total} checks"
            ),
            (
                f"  Price ${self._number(record, 'price', '.2f')} | "
                f"{level_type} ${self._number(record, 'level_price', '.2f')} | "
                f"{self._number(record, 'distance_pct', '.2%', '')} from level"
            ),
            (
                f"  RVOL {self._number(record, 'relative_volume', '.2f', 'x')} | "
                f"Relative strength {self._number(record, 'relative_strength', '.2%', '')} | "
                f"Reward/risk {self._number(record, 'reward_risk', '.2f')}"
            ),
            f"  Confirmed: {self._checks(record, 'passed_checks')}",
            f"  Still missing: {self._checks(record, 'missing_checks')}",
        ]
        return "\n".join(lines)


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ExcludeCandidateFilter())
    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter())
    console.addFilter(ExcludeCandidateFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler, console], force=True)

    candidate_formatter = CandidateFormatter()
    candidate_path = path.with_name("candidates.log")
    candidate_file = RotatingFileHandler(
        candidate_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    candidate_file.setFormatter(candidate_formatter)
    candidate_console = logging.StreamHandler()
    candidate_console.setFormatter(candidate_formatter)
    candidate_logger = logging.getLogger("tradingpilot.strategy.candidates")
    for existing in candidate_logger.handlers:
        existing.close()
    candidate_logger.handlers.clear()
    candidate_logger.addHandler(candidate_file)
    candidate_logger.addHandler(candidate_console)
    candidate_logger.setLevel(logging.INFO)
    candidate_logger.propagate = True

    # httpx logs full request URLs at INFO. Telegram embeds the bot token in its URL, so
    # dependency request logging must stay below the application's structured event boundary.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
