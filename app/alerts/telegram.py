from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

import httpx

from app.alerts.formatter import format_transition
from app.options.models import OptionCandidate
from app.strategy.models import StateTransition


class AlertDispatcher:
    def __init__(
        self, token: str | None = None, chat_id: str | None = None, cooldown_seconds: int = 300
    ):
        self.token = token
        self.chat_id = chat_id
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.last_sent: dict[tuple[str, str, str], datetime] = {}
        self.log = logging.getLogger("tradingpilot.alerts")

    async def send(
        self, transition: StateTransition, option: OptionCandidate | None = None
    ) -> bool:
        key = (transition.symbol, transition.direction.value, transition.to_state.value)
        previous = self.last_sent.get(key)
        if previous and transition.timestamp - previous < self.cooldown:
            self.log.info(
                "alert_deduplicated",
                extra={"event": "alert_deduplicated", "symbol": transition.symbol},
            )
            return False
        message = format_transition(transition, option)
        encoding = sys.stdout.encoding or "utf-8"
        console_message = message.encode(encoding, errors="replace").decode(encoding)
        print(console_message, flush=True)
        if self.token and self.chat_id:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        url, json={"chat_id": self.chat_id, "text": message}
                    )
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                self.log.error(
                    "telegram_delivery_failed",
                    extra={
                        "event": "telegram_delivery_failed",
                        "symbol": transition.symbol,
                        "state": type(exc).__name__,
                    },
                )
                return False
        self.last_sent[key] = transition.timestamp
        return True
