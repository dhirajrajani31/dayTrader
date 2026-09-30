from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.market_data.models import SessionPhase


def session_phase(timestamp: datetime, timezone: str = "America/Chicago") -> SessionPhase:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    local = timestamp.astimezone(ZoneInfo(timezone))
    if local.weekday() >= 5:
        return SessionPhase.CLOSED
    current = local.time().replace(tzinfo=None)
    if time(3, 0) <= current < time(8, 30):
        return SessionPhase.PREMARKET
    if time(8, 30) <= current < time(15, 0):
        return SessionPhase.REGULAR_MARKET
    if time(15, 0) <= current < time(19, 0):
        return SessionPhase.AFTER_HOURS
    return SessionPhase.CLOSED
