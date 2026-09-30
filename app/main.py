from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.alerts.telegram import AlertDispatcher
from app.config.settings import Settings
from app.logging_config import configure_logging
from app.market_data.auth import token_manager_from_settings
from app.market_data.tastytrade import TastytradeMarketDataProvider
from app.operations.preflight import CheckStatus, print_results, run_market_check
from app.runtime import LiveStrategyCoordinator, MarketMonitor
from app.shadow.outcome_engine import outcome_summary
from app.shadow.tracker import ShadowTracker
from app.storage.database import create_database
from app.storage.repository import Repository
from app.strategy.engine import StrategyEngine
from app.strategy.models import Direction, SetupState, StateTransition
from app.strategy.scenarios import scenario
from app.watchlist.manager import WatchlistManager


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.main", description="TradingPilot shadow-only signal pilot"
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run deterministic replay scenarios without credentials")
    commands.add_parser("run", help="connect to read-only tastytrade market data")
    market_check = commands.add_parser(
        "market-check", help="verify OAuth, REST, DXLink, candles, Telegram, and SQLite"
    )
    market_check.add_argument("--stream-seconds", type=float, default=10)
    market_check.add_argument("--send-telegram", action="store_true")
    watchlist = commands.add_parser("watchlist", help="replace today's watchlist")
    watchlist.add_argument("symbols", nargs="+")
    commands.add_parser("status", help="print persisted local status")
    commands.add_parser("init-db", help="initialize the SQLite schema")
    return root


def dependencies(settings: Settings) -> tuple[Repository, WatchlistManager]:
    _, sessions = create_database(settings.database_url)
    return Repository(sessions), WatchlistManager(settings.watchlist_file, settings.benchmarks)


async def demo(settings: Settings, repository: Repository) -> None:
    settings.assert_shadow_mode()
    # Synthetic replays are a local validation tool. Never forward DEMO symbols to Telegram,
    # even when live alert credentials are present in the environment.
    alerts = AlertDispatcher(cooldown_seconds=settings.strategy.alert_cooldown_seconds)
    tracker = ShadowTracker()

    async def transition_handler(transition: StateTransition) -> None:
        repository.save_transition(transition)
        await alerts.send(transition)

    cases = [
        ("Bullish good retest", Direction.BULLISH, "triggered"),
        ("Bullish failed retest", Direction.BULLISH, "invalidated"),
        ("Bullish runaway", Direction.BULLISH, "extended"),
        ("Bearish failed reclaim", Direction.BEARISH, "triggered"),
        ("Bearish reclaim", Direction.BEARISH, "invalidated"),
    ]
    for index, (label, direction, outcome) in enumerate(cases, start=1):
        print(f"\n=== {label} ===")
        engine = StrategyEngine(settings.strategy, transition_handler)
        for observation in scenario(outcome, direction, f"DEMO{index}"):
            await engine.evaluate(observation)
        if engine.signals:
            signal = engine.signals[-1]
            repository.save_signal(signal)
            repository.save_shadow_trade(
                signal, scenario(outcome, direction, f"DEMO{index}")[-1].model_dump(mode="json")
            )
            tracker.open(signal)
        final = next(iter(engine.machines.values())).state
        print(f"FINAL STATE: {final.value}")
    expected = [
        SetupState.TRIGGERED,
        SetupState.INVALIDATED,
        SetupState.EXTENDED,
        SetupState.TRIGGERED,
        SetupState.INVALIDATED,
    ]
    print(f"\nReplay complete. Expected finals: {', '.join(state.value for state in expected)}")


async def run_live(settings: Settings, repository: Repository, manager: WatchlistManager) -> None:
    settings.assert_shadow_mode()
    if not settings.has_tastytrade_credentials:
        raise SystemExit(
            "tastytrade OAuth credentials are required for run; use 'demo' without credentials"
        )
    symbols = manager.load()
    repository.save_watchlist(symbols, datetime.now(settings.tz), "file")
    alerts = AlertDispatcher(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        settings.strategy.alert_cooldown_seconds,
    )
    tracker = ShadowTracker()
    trade_ids: dict[str, int] = {}
    last_health_recorded: datetime | None = None

    async def transition_handler(transition: StateTransition) -> None:
        repository.save_transition(transition)
        if transition.to_state == SetupState.TRIGGERED:
            signal = engine.signals[-1]
            repository.save_signal(signal)
            trade_ids[signal.symbol] = repository.save_shadow_trade(
                signal, transition.observation.model_dump(mode="json")
            )
            tracker.open(signal, transition.observation.model_dump(mode="json"))
        await alerts.send(transition)

    async def completed_candle(candle) -> None:
        nonlocal last_health_recorded
        trade = tracker.update(candle.symbol, candle.timestamp, candle.close)
        trade_id = trade_ids.get(candle.symbol)
        if trade and trade_id:
            repository.save_outcome(trade_id, candle.timestamp, outcome_summary(trade))
        if (
            last_health_recorded is None
            or (candle.timestamp - last_health_recorded).total_seconds() >= 30
        ):
            repository.application_event(
                candle.timestamp,
                "market_data_heartbeat",
                "normalized market data received",
                {"connection_state": provider.connection_state.value},
            )
            last_health_recorded = candle.timestamp

    async def save_snapshot(payload: dict) -> None:
        repository.save_feature_snapshot(payload)

    engine = StrategyEngine(settings.strategy, transition_handler)
    coordinator = LiveStrategyCoordinator(engine, settings.strategy)
    auth = token_manager_from_settings(settings)
    logging.getLogger("tradingpilot").info("startup", extra={"event": "startup"})
    repository.application_event(datetime.now(settings.tz), "startup", "TradingPilot started")
    try:
        while True:
            provider = TastytradeMarketDataProvider(auth, settings.strategy.stale_after_seconds)
            monitor = MarketMonitor(
                provider,
                settings.strategy.stale_after_seconds,
                coordinator,
                completed_candle,
            )
            warmup_start = datetime.now(settings.tz) - timedelta(
                days=settings.historical_lookback_days
            )
            try:
                history = await provider.warm_up(
                    symbols,
                    start=warmup_start,
                    timeout_seconds=settings.historical_warmup_timeout_seconds,
                )
                monitor.seed_candles(history)
                repository.application_event(
                    datetime.now(settings.tz),
                    "historical_warmup",
                    "DXLink candle warm-up completed",
                    {symbol: len(candles) for symbol, candles in history.items()},
                )
            except Exception:
                logging.getLogger("tradingpilot").exception(
                    "historical_warmup_failed",
                    extra={"event": "historical_warmup_failed"},
                )
            monitor_task = asyncio.create_task(monitor.run(symbols, save_snapshot))
            watchlist_task = asyncio.create_task(_wait_for_watchlist_change(manager, symbols))
            tasks = {monitor_task, watchlist_task}
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await provider.close()
            if monitor_task in done:
                await monitor_task
                return
            symbols = watchlist_task.result()
            repository.save_watchlist(symbols, datetime.now(settings.tz), "runtime_file")
            repository.application_event(
                datetime.now(settings.tz),
                "watchlist_changed",
                "runtime watchlist changed",
                {"symbols": symbols},
            )
            logging.getLogger("tradingpilot").info(
                "watchlist_changed", extra={"event": "watchlist_changed"}
            )
    finally:
        repository.application_event(datetime.now(settings.tz), "shutdown", "TradingPilot stopped")
        logging.getLogger("tradingpilot").info("shutdown", extra={"event": "shutdown"})


async def _wait_for_watchlist_change(manager: WatchlistManager, current: list[str]) -> list[str]:
    while True:
        await asyncio.sleep(2)
        updated = manager.reload_if_changed()
        if updated is not None and updated != current:
            return updated


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings()
    settings.assert_shadow_mode()
    configure_logging(settings.log_file)
    repository, manager = dependencies(settings)
    if args.command == "demo":
        asyncio.run(demo(settings, repository))
    elif args.command == "run":
        asyncio.run(run_live(settings, repository, manager))
    elif args.command == "market-check":
        results = asyncio.run(
            run_market_check(
                settings,
                repository,
                manager,
                stream_seconds=args.stream_seconds,
                send_telegram=args.send_telegram,
            )
        )
        print_results(results)
        return 1 if any(result.status == CheckStatus.FAIL for result in results) else 0
    elif args.command == "watchlist":
        symbols = manager.replace(args.symbols)
        repository.save_watchlist(symbols, datetime.now(settings.tz), "cli")
        print("Watchlist:", " ".join(symbols))
    elif args.command == "status":
        print(json.dumps(repository.status(), indent=2, default=str))
    elif args.command == "init-db":
        print("SQLite schema initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
