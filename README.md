# TradingPilot

TradingPilot is a local, Windows-first intraday signal pilot. It monitors a user-supplied
watchlist, evaluates deterministic breakout/retest rules, sends state-change alerts, and records
hypothetical outcomes in SQLite. It does **not** submit brokerage orders and contains no order
submission API.

The current strategy identity is `MR_INVESTR_BASELINE` version `0.1.0`. Historical rows retain
that identity so later rule versions can coexist.

## Safety boundary

`TRADING_MODE=SHADOW` is validated at configuration startup. Any other value aborts the program.
The tastytrade adapter implements the read-only market-data interface only: quote lookup, DXLink
quote/trade streaming, and DXLink historical one-minute candles. Option data remains an isolated
placeholder until verified. There is no order model, order endpoint, dry-run order endpoint, or
execution module.

## Architecture and data flow

```text
watchlist.txt / CLI (+ SPY, QQQ)
              |
              v
MarketDataProvider -- Synthetic replay or read-only tastytrade adapter
              |
              v
normalized QuoteEvent / TradeEvent / CandleEvent
              |
              v
1m candle builder -> feature quality (READY / PARTIAL / STALE)
              |
              v
level zones + benchmark context -> deterministic rule engine
              |
              v
WATCHING -> ARMED -> WAITING_FOR_RETEST
                         |       |       |
                         v       v       v
                    TRIGGERED INVALIDATED EXTENDED
                         |
              +----------+----------+
              |                     |
       console/Telegram       SQLite ShadowTrade
                                   + outcomes
```

The packages are separated by responsibility:

- `app/market_data`: provider contract, normalized events, synthetic provider, tastytrade adapter;
- `app/strategy`: features, level zones, rules, explicit state machine, engine and replay scenarios;
- `app/options`: hypothetical option candidate model and liquidity/delta selector;
- `app/alerts`: formatting, console delivery, Telegram cooldown/deduplication;
- `app/shadow`: in-memory MFE/MAE, checkpoints, targets and stop tracking;
- `app/storage`: SQLAlchemy schema and SQLite repository;
- `app/watchlist`: normalized file/CLI watchlist with automatic SPY and QQQ;
- `app/market_context`: benchmark context and the deliberately unavailable sector extension point.

Market-data errors are contained in the adapter. DXLink reconnects with exponential backoff up to
60 seconds, connection changes are logged, and stale normalized data is marked `STALE`. The live
process checks `watchlist.txt` every two seconds; changes restart the read-only subscription with
the new list.

## Strategy rules

The first-generation state machine does not trigger on a VWAP cross, level touch, or breakout by
itself. Arming requires all of the following:

- a meaningful level zone nearby;
- relative volume at or above the configured minimum;
- directional relative strength/weakness;
- VWAP or opening-range context;
- enough measured room to the next observed level.

Moving from `ARMED` to `WAITING_FOR_RETEST` requires a buffered break and confirmed volume. A
trigger then requires a retest/failed reclaim, held structure, continuation, and volume. Loss of
structure becomes `INVALIDATED`. Moving too far from the level before the retest becomes
`EXTENDED` and emits `DO NOT CHASE`.

The exact level is pinned when a setup arms, so a newly detected nearby swing cannot silently
change an in-flight setup. Thresholds live in `StrategySettings` and may be overridden with
`STRATEGY_...` environment variables, for example
`STRATEGY_EXTENSION_THRESHOLD_PCT=0.01`.

Market agreement is recorded but is not a hard veto. The synthetic scenarios explicitly cover
bullish and bearish success, invalidation, extension, bare VWAP crossing, and unconfirmed support.

## Windows installation

From PowerShell in the repository root, using Python 3.12 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The `tzdata` dependency is intentional: it makes `zoneinfo` and daylight-saving-aware
`America/Chicago` timestamps reliable on Windows.

Initialize and inspect SQLite:

```powershell
python -m app.main init-db
python -m app.main status
```

Database and rotating structured logs default to:

- `data/tradingpilot.db`
- `logs/tradingpilot.log`

Both generated file types are gitignored.

## Demo mode (no credentials)

```powershell
python -m app.main demo
```

The demo always prints alerts to the console. It replays five deterministic lifecycles and ends
in these states: bullish `TRIGGERED`, bullish `INVALIDATED`, bullish `EXTENDED`, bearish
`TRIGGERED`, and bearish `INVALIDATED`. Triggered cases write signals and shadow trades to SQLite.
On older Windows code pages, emoji may display as `?`; Telegram keeps the original Unicode.

## Watchlist

Edit `watchlist.txt`, one ticker per line, or replace it from the CLI:

```powershell
python -m app.main watchlist MSTR COIN CRCL PLTR
```

Blank lines, comments beginning with `#`, and duplicates are ignored. Symbols are uppercased.
`SPY` and `QQQ` are always included. A running process detects file changes and reconnects the
subscription safely.

## Candidate visibility

Telegram is reserved for strategy state transitions such as `ARMED`, `WAITING FOR RETEST`, and
`TRIGGERED`. The local JSON log also emits an `interesting_candidate` event before a transition
when a symbol is near an important level and passes at least three of the five arming checks. Each
event includes direction, price, level, distance, relative volume, relative strength, reward/risk,
and the passed and missing checks. An unchanged candidate is logged at most once every five
minutes; a changed level or confirmation set is logged immediately.

Candidate events are written as readable five-line summaries to `logs/candidates.log` and printed
in the same format in the running console. Watch them live in a second PowerShell window:

```powershell
Get-Content .\logs\candidates.log -Wait
```

The generic per-minute feature snapshots remain stored in SQLite but are not written at INFO
level. Candidate events are kept out of the machine-oriented JSON log, keeping both feeds focused.

## Telegram

Create a Telegram bot and put these values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

If either is absent, console alerts still work. Alerts are deduplicated by symbol, direction and
destination state with a configurable cooldown. Network delivery failures are logged and never
stop strategy evaluation.

## tastytrade market data

The verified adapter follows the official current API flow rather than assuming a username and
password schema:

1. At `my.tastytrade.com`, create a personal OAuth application with only the `read` scope. Do not
   select `trade`; `openid` is unnecessary. If a redirect is required, a full local placeholder
   such as `https://localhost/tradingpilot/oauth/callback` is sufficient for a personal grant.
2. Save the client secret when shown, create a personal grant, and save its refresh token.
3. Put the credentials in `.env`:

   ```dotenv
   TASTYTRADE_ENVIRONMENT=production
   TASTYTRADE_CLIENT_ID=...
   TASTYTRADE_CLIENT_SECRET=...
   TASTYTRADE_REFRESH_TOKEN=...
   ```

4. Run the non-trading operational preflight:

   ```powershell
   python -m app.main market-check --stream-seconds 15
   ```

5. Run `python -m app.main run` or `scripts\run_tradingpilot.bat`.

It uses the documented production REST base URL, required `User-Agent`,
`GET /api-quote-tokens`, and the DXLink `SETUP` / `AUTH` / `CHANNEL_REQUEST` / `FEED_SETUP` /
`FEED_SUBSCRIPTION` sequence for `Quote` and `Trade`. See the official
[streaming guide](https://developer.tastytrade.com/docs/guides/stream-market-data/) and
[market-data guide](https://developer.tastytrade.com/docs/concepts/market-data/), and
[OAuth guide](https://developer.tastytrade.com/oauth/).

TradingPilot automatically exchanges the long-lived refresh token for a 15-minute access token,
caches it, refreshes one minute before expiry, and retries one authenticated request after a `401`.
Refreshing is concurrency-safe, and secrets/tokens are never logged. A manually supplied
`TASTYTRADE_ACCESS_TOKEN` remains available as a short-lived diagnostic fallback.

Before each live subscription, TradingPilot requests five calendar days of verified DXLink
one-minute Candle history. It uses this to populate previous-day high/low/close, premarket levels,
opening ranges, observed session levels, swings, and time-of-day volume baselines. If candle
warm-up is unavailable or incomplete, the error is logged and those inputs remain unavailable;
the strategy never invents them.

`market-check` verifies SHADOW mode, SQLite, the watchlist, OAuth minting, the official equity
market-session endpoint, an SPY REST quote, historical SPY candles, and a live SPY/QQQ DXLink
event. Telegram is validated when configured; add `--send-telegram` to send an explicit test
message. A quiet/closed live stream is reported as a warning, while authentication, REST, or
historical failures make the command exit nonzero.

## Shadow trades and persistence

On `TRIGGERED`, the program stores the underlying entry, direction, version, invalidation, targets,
full entry observation and benchmark-related inputs. Completed candles update 5/15/30/60-minute
checkpoints, maximum favorable/adverse excursion, stop/target flags, target times and estimated R.
Outcomes are snapshots, not orders. SQLite stores normalized feature snapshots and transition
contexts, not every raw market tick.

Tables are:

- `watchlist_sessions`
- `market_feature_snapshots`
- `strategy_states`
- `state_transitions`
- `signals`
- `shadow_trades`
- `shadow_trade_outcomes`
- `application_events`

## Validation

```powershell
python -m pytest -q
ruff check .
mypy app
python -m app.main demo
python -m app.main market-check --stream-seconds 15
```

## Known limitations and next phases

In priority order:

1. Complete multi-session reconnect/refresh soak testing against the verified read-only
   production grant.
2. Verify option-chain metadata plus option quote/Greeks subscriptions; then connect the existing
   selector. Until then triggered alerts explicitly say option selection is unavailable.
3. Use the verified market-session endpoint in the runtime scheduler for holidays and half-days.
   The current session helper handles
   weekdays, Chicago time, and DST but not exchange holidays.
4. Add durable recovery for in-flight state machines and active shadow trades after a restart.
5. Replace per-candle outcome snapshot inserts with update/compaction for longer pilot runs.
6. Add retention policy, database migrations, and more detailed feed-health metrics.
7. Add verified sector ETF context; no sector mapping is guessed in this version.

Do not treat the current thresholds or synthetic results as evidence of profitability. Run shadow
mode through varied market regimes and analyze versioned outcomes before changing rules.
