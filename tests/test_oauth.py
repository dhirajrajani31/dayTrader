import json

import httpx
import pytest

from app.market_data.auth import OAuthTokenManager


@pytest.mark.asyncio
async def test_refresh_token_is_cached_and_secrets_are_sent_in_json():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload == {
            "grant_type": "refresh_token",
            "refresh_token": "refresh-secret",
            "client_secret": "client-secret",
            "client_id": "client-id",
        }
        assert request.headers["user-agent"] == "TradingPilot/0.1.0"
        return httpx.Response(200, json={"access_token": "access-1", "expires_in": 900})

    manager = OAuthTokenManager(
        "https://example.test",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-secret",
        transport=httpx.MockTransport(handler),
    )
    assert await manager.get_access_token() == "access-1"
    assert await manager.get_access_token() == "access-1"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_authenticated_request_refreshes_once_after_401():
    refresh_count = 0
    resource_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_count, resource_count
        if request.url.path == "/oauth/token":
            refresh_count += 1
            return httpx.Response(
                200,
                json={"access_token": f"access-{refresh_count}", "expires_in": 900},
            )
        resource_count += 1
        if request.headers["authorization"] == "Bearer access-1":
            return httpx.Response(401, json={"error": {"code": "unauthorized"}})
        return httpx.Response(200, json={"ok": True})

    manager = OAuthTokenManager(
        "https://example.test",
        client_secret="client-secret",
        refresh_token="refresh-secret",
        transport=httpx.MockTransport(handler),
    )
    response = await manager.request("GET", "/resource")
    assert response.json() == {"ok": True}
    assert refresh_count == 2
    assert resource_count == 2


@pytest.mark.asyncio
async def test_static_access_token_fallback_does_not_refresh():
    manager = OAuthTokenManager("https://example.test", access_token="temporary")
    assert await manager.get_access_token() == "temporary"
    assert not manager.can_refresh
