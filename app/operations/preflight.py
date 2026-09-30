from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import httpx

from app.config.settings import Settings
from app.market_data.auth import token_manager_from_settings
from app.market_data.tastytrade import TastytradeMarketDataProvider
from app.options.models import OptionCandidate
from app.options.selector import select_option
from app.storage.repository import Repository
from app.strategy.models import Direction
from app.watchlist.manager import WatchlistManager


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


async def run_market_check(
    settings: Settings,
    repository: Repository,
    watchlist: WatchlistManager,
    *,
    stream_seconds: float = 10,
    send_telegram: bool = False,
) -> list[CheckResult]:
    """Run non-trading operational checks. No brokerage write endpoint is called."""
    results: list[CheckResult] = []
    try:
        settings.assert_shadow_mode()
        repository.status()
        results.append(
            CheckResult("shadow/database", CheckStatus.PASS, "SHADOW mode; SQLite ready")
        )
    except Exception as exc:
        results.append(CheckResult("shadow/database", CheckStatus.FAIL, str(exc)))

    symbols = watchlist.load()
    results.append(
        CheckResult(
            "watchlist",
            CheckStatus.PASS if symbols else CheckStatus.FAIL,
            f"{len(symbols)} symbols: {', '.join(symbols)}",
        )
    )

    results.append(await _check_telegram(settings, send_telegram))
    if not settings.has_tastytrade_credentials:
        results.append(
            CheckResult(
                "tastytrade OAuth",
                CheckStatus.FAIL,
                "configure TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN",
            )
        )
        return results

    auth = token_manager_from_settings(settings)
    provider = TastytradeMarketDataProvider(
        auth, settings.strategy.stale_after_seconds, settings.options
    )
    try:
        await auth.get_access_token()
        mode = "refresh-token flow" if auth.can_refresh else "static access-token fallback"
        results.append(CheckResult("tastytrade OAuth", CheckStatus.PASS, mode))
    except Exception as exc:
        results.append(CheckResult("tastytrade OAuth", CheckStatus.FAIL, str(exc)))
        await provider.close()
        return results

    try:
        session = await provider.get_market_session()
        session_state = str(session.get("state", "unknown"))
        results.append(CheckResult("market calendar", CheckStatus.PASS, session_state))
    except Exception as exc:
        results.append(CheckResult("market calendar", CheckStatus.FAIL, str(exc)))

    spy_price: float | None = None
    try:
        quote = await provider.get_quote("SPY")
        if quote and quote.last is not None:
            spy_price = quote.last
            detail = f"SPY last={quote.last:.2f} as-of={quote.timestamp.isoformat()}"
            results.append(CheckResult("REST quote", CheckStatus.PASS, detail))
        else:
            results.append(CheckResult("REST quote", CheckStatus.FAIL, "SPY quote unavailable"))
    except Exception as exc:
        results.append(CheckResult("REST quote", CheckStatus.FAIL, str(exc)))

    try:
        chain = await provider.get_option_chain("SPY")
        complete = [
            item
            for item in chain
            if item.bid is not None and item.ask is not None and item.delta is not None
        ]
        candidates = [
            OptionCandidate(
                **item.model_dump(exclude={"timestamp"}),
                quote_timestamp=item.timestamp,
            )
            for item in complete
        ]
        selected_call = (
            select_option(candidates, spy_price, Direction.BULLISH, settings.options)
            if spy_price is not None
            else None
        )
        selected_put = (
            select_option(candidates, spy_price, Direction.BEARISH, settings.options)
            if spy_price is not None
            else None
        )
        if selected_call and selected_put:
            results.append(
                CheckResult(
                    "option data",
                    CheckStatus.PASS,
                    (
                        f"{len(complete)} contracts enriched; selector chose "
                        f"{selected_call.symbol} and {selected_put.symbol}"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    "option data",
                    CheckStatus.FAIL,
                    "could not select both a call and put under configured DTE/delta/spread rules",
                )
            )
    except Exception as exc:
        results.append(CheckResult("option data", CheckStatus.FAIL, str(exc)))

    try:
        history = await provider.warm_up(
            ["SPY"],
            start=datetime.now(settings.tz) - timedelta(days=2),
            timeout_seconds=max(stream_seconds, 2),
        )
        count = len(history.get("SPY", []))
        candles = history.get("SPY", [])
        period = (
            f" from {candles[0].timestamp.isoformat()} to {candles[-1].timestamp.isoformat()}"
            if candles
            else ""
        )
        results.append(
            CheckResult(
                "historical candles",
                CheckStatus.PASS if count else CheckStatus.FAIL,
                f"received {count} one-minute SPY candles{period}",
            )
        )
    except Exception as exc:
        results.append(CheckResult("historical candles", CheckStatus.FAIL, str(exc)))

    stream = provider.subscribe(["SPY", "QQQ"])
    try:
        event = await asyncio.wait_for(anext(stream), timeout=stream_seconds)
        results.append(
            CheckResult(
                "DXLink stream",
                CheckStatus.PASS,
                f"received {type(event).__name__} for {event.symbol}",
            )
        )
    except TimeoutError:
        results.append(
            CheckResult(
                "DXLink stream",
                CheckStatus.WARN,
                f"no event within {stream_seconds:g}s; feed may be quiet or market closed",
            )
        )
    except Exception as exc:
        results.append(CheckResult("DXLink stream", CheckStatus.FAIL, str(exc)))
    finally:
        close_stream = getattr(stream, "aclose", None)
        if close_stream:
            await close_stream()
        await provider.close()
    return results


async def _check_telegram(settings: Settings, send_message: bool) -> CheckResult:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return CheckResult(
            "Telegram", CheckStatus.WARN, "not configured; console alerts remain enabled"
        )
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{base}/getMe")
            response.raise_for_status()
            if not response.json().get("ok"):
                return CheckResult("Telegram", CheckStatus.FAIL, "Bot API returned ok=false")
            if send_message:
                sent = await client.post(
                    f"{base}/sendMessage",
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": (
                            "TradingPilot market-check passed Telegram connectivity. SHADOW MODE."
                        ),
                    },
                )
                sent.raise_for_status()
        detail = "bot valid; test message sent" if send_message else "bot valid; no message sent"
        return CheckResult("Telegram", CheckStatus.PASS, detail)
    except httpx.HTTPStatusError as exc:
        return CheckResult(
            "Telegram", CheckStatus.FAIL, f"Bot API returned HTTP {exc.response.status_code}"
        )
    except httpx.RequestError as exc:
        return CheckResult("Telegram", CheckStatus.FAIL, type(exc).__name__)
    except (ValueError, TypeError) as exc:
        return CheckResult("Telegram", CheckStatus.FAIL, type(exc).__name__)


def print_results(results: list[CheckResult]) -> None:
    width = max((len(result.name) for result in results), default=0)
    for result in results:
        print(f"{result.status.value:4}  {result.name:<{width}}  {result.detail}")
    passed = sum(result.status == CheckStatus.PASS for result in results)
    warned = sum(result.status == CheckStatus.WARN for result in results)
    failed = sum(result.status == CheckStatus.FAIL for result in results)
    print(f"\nSummary: {passed} passed, {warned} warnings, {failed} failed")
