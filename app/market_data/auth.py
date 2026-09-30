from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.market_data.base import MarketDataError

if TYPE_CHECKING:
    from app.config.settings import Settings


class OAuthTokenManager:
    """Concurrency-safe tastytrade OAuth access-token lifecycle.

    Refresh tokens and client secrets are held in memory, never logged, and sent only to the
    documented `/oauth/token` endpoint. A static access token remains supported for diagnostics.
    """

    user_agent = "TradingPilot/0.1.0"

    def __init__(
        self,
        base_url: str,
        *,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._access_token = access_token or None
        self.client_id = client_id or None
        self.client_secret = client_secret or None
        self.refresh_token = refresh_token or None
        self.transport = transport
        self._expires_at_monotonic = 0.0
        self._lock = asyncio.Lock()
        self.log = logging.getLogger("tradingpilot.market_data.oauth")

    @property
    def can_refresh(self) -> bool:
        return bool(self.client_secret and self.refresh_token)

    @property
    def configured(self) -> bool:
        return bool(self._access_token or self.can_refresh)

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not self.configured:
            raise MarketDataError(
                "tastytrade credentials missing: configure refresh credentials or an access token"
            )
        if not self.can_refresh:
            if not self._access_token:
                raise MarketDataError("TASTYTRADE_ACCESS_TOKEN is missing")
            return self._access_token
        if (
            not force_refresh
            and self._access_token
            and time.monotonic() < self._expires_at_monotonic - 60
        ):
            return self._access_token
        async with self._lock:
            if (
                not force_refresh
                and self._access_token
                and time.monotonic() < self._expires_at_monotonic - 60
            ):
                return self._access_token
            return await self._refresh()

    async def _refresh(self) -> str:
        payload: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token or "",
            "client_secret": self.client_secret or "",
        }
        if self.client_id:
            payload["client_id"] = self.client_id
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={"User-Agent": self.user_agent},
                timeout=10,
                transport=self.transport,
            ) as client:
                response = await client.post("/oauth/token", json=payload)
                response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            if not isinstance(token, str) or not token:
                raise MarketDataError("OAuth response did not contain an access_token")
            expires_in = float(data.get("expires_in", 900))
            self._access_token = token
            self._expires_at_monotonic = time.monotonic() + max(expires_in, 60)
            self.log.info("oauth_token_refreshed", extra={"event": "oauth_token_refreshed"})
            return token
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise MarketDataError(f"tastytrade OAuth refresh failed: {exc}") from exc

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> httpx.Response:
        """Make one authenticated request, refreshing and retrying once after HTTP 401."""
        for attempt in range(2):
            token = await self.get_access_token(force_refresh=attempt == 1)
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": self.user_agent,
                },
                timeout=timeout,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, params=params, json=json_body)
            if response.status_code != 401 or not self.can_refresh or attempt == 1:
                response.raise_for_status()
                return response
        raise MarketDataError("authenticated request failed")


def token_manager_from_settings(settings: Settings) -> OAuthTokenManager:
    return OAuthTokenManager(
        settings.tastytrade_base_url,
        access_token=settings.tastytrade_access_token,
        client_id=settings.tastytrade_client_id,
        client_secret=settings.tastytrade_client_secret,
        refresh_token=settings.tastytrade_refresh_token,
    )
