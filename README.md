# TradingPilot

TradingPilot is a local, Windows-first intraday signal pilot. It monitors a user-supplied
watchlist, evaluates deterministic breakout/retest rules, sends state-change alerts, and records
hypothetical outcomes in SQLite. It does **not** submit brokerage orders and contains no order
submission API.

The current strategy identity is `MR_INVESTR_BASELINE` version `0.2.0`. Historical rows retain
that identity so later rule versions can coexist.

## Safety boundary

`TRADING_MODE=SHADOW` is validated at configuration startup. Any other value aborts the program.
The tastytrade adapter implements read-only market data only: quotes, DXLink quote/trade
streaming, historical one-minute candles, nested option chains, and option Quote/Trade/Greeks/
Summary data. There is no order model, order endpoint, dry-run order endpoint, or live execution
module. An "option trade" always means a local simulated fill in SQLite.

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
              +----------+----------------+
              |                           |
       console/Telegram        listed option selection
                                   |
                         ask entry -> bid marks/exit
                                   |
                      SQLite underlying + option P&L
```

The packages are separated by responsibility:

- `app/market_data`: provider contract, normalized events, synthetic provider, tastytrade adapter;
- `app/strategy`: features, level zones, rules, explicit state machine, engine and replay scenarios;
- `app/options`: listed-contract model and deterministic liquidity/delta selector;
- `app/alerts`: formatting, console delivery, Telegram cooldown/deduplication;
- `app/shadow`: candle-range MFE/MAE plus conservative option fill/P&L tracking;
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

Optional dated plans live in `watchlist_context.json`. A plan distinguishes a decision level
from its intended destinations; it does not treat every number as an unrelated entry. For
example, `228.79 -> 232` means the normal volume-confirmed breakout and retest occurs around
228.79, after which 232 becomes the first shadow-trade target. Plans apply only on their exact
`session_date`, expire automatically afterward, and never bypass the existing relative-volume,
relative-strength, VWAP/opening-range, room, breakout, or retest checks.
The decision level can be approached from below for a breakout or from above for a bullish
retest/bounce; once armed, its ordered destinations remain pinned through the setup lifecycle.

Each symbol can have alternative triggers and up to two ordered targets:

```json
{
  "session_date": "2026-10-01",
  "plans": {
    "NVDA": {
      "note": "If 228.79 is reached and confirmed, look for continuation to 232.",
      "triggers": [{"price": 228.79, "targets": [232.0]}]
    }
  }
}
```

Telegram alerts, candidate logs, and stored entry observations include the planned path. A target
is an evaluation point for the simulated trade, not a prediction or order instruction.

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

Option selection uses the official
[nested option-chain endpoint](https://developer.tastytrade.com/reference/instruments/getOptionChainsSymbolNested/),
the broker-provided OCC and streamer symbols, the
[market-data endpoint](https://developer.tastytrade.com/reference/market-data/getMarketDataByType/),
and DXLink Quote, Trade, Greeks, and Summary events. Contract symbols are never synthesized.

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
event. It also downloads the official nested SPY option chain, enriches contracts with quote and
Greeks data, and requires the configured selector to choose both a call and a put. Telegram is
validated when configured; add `--send-telegram` to send an explicit test message. A quiet/closed
live stream is reported as a warning, while authentication, REST, option, or historical failures
make the command exit nonzero.

## Shadow trades and persistence

On `TRIGGERED`, the program stores the underlying entry, direction, version, invalidation, targets,
full entry observation and benchmark-related inputs. It then requests the listed option chain and
selects a call for bullish signals or a put for bearish signals. The default policy uses 1-7 DTE,
0.55-0.70 absolute delta, no more than a 20% quoted spread, and one contract. It records bid/ask,
delta, gamma, theta, IV, open interest, and volume at entry.

The simulated entry is the displayed ask. Every completed one-minute candle marks liquidation at
the displayed bid, so spread cost is included rather than assuming midpoint fills. The position
closes at bid when the underlying reaches target 1, breaches invalidation, or reaches the 60-minute
time limit. If target and stop occur in the same one-minute candle, the conservative stop outcome
wins. This is execution policy `OPTION_SHADOW_V1`; changing assumptions requires a new version.

Net P&L additionally subtracts the current $1 stock/ETF option opening commission shown on
[tastytrade's pricing page](https://tastytrade.com/pricing/), configurable estimated opening and
closing fees, and a configurable one-cent option-price slippage stress on each side. Ancillary
fees are estimates until reconciled against real brokerage statements; all assumptions are exposed
as `OPTION_...` settings in `.env.example`.

Entry and exit execution attempts are stored with `SUCCESS`, `DELAYED`, or `MISSED` status and a
machine-readable reason. A stop, target, or time exit remains pending if its option quote is absent,
invalid, or stale. The trade closes at the first later valid bid and records the delay instead of
silently losing the exit event. Pending exits survive process and session-date restarts.

Underlying MFE/MAE, target, and stop detection use each completed candle's high and low—not only
its close—fixing the prior intraminute blind spot. Exact ordering inside a one-minute candle is not
available. A restart restores today's state machines and open underlying/option shadow trades from
SQLite. It cannot reconstruct market movement that occurred while the program was stopped.

Inspect recent trades at any time:

```powershell
python -m app.main trades --limit 10
```

The report shows the underlying setup and outcome, exact OCC contract, entry Greeks and
liquidity, conservative liquidation value, unrealized/realized P&L, and exit reason. These are
simulations and never brokerage orders.

## Daily evaluation scorecard

The scorecard groups timestamps by Chicago trading date and reports the complete funnel: armed
setups, triggers, option trades, closed/open trades, entry coverage, missed or delayed entries,
wins/losses, gross and net P&L, expectancy, profit factor, maximum drawdown, exit reasons, missed
or delayed exits, and quote-mark quality.

```powershell
# Today's scorecard
python -m app.main scorecard

# A specific Chicago trading date
python -m app.main scorecard --date 2026-10-01

# Recent period or all recorded history
python -m app.main scorecard --days 20
python -m app.main scorecard --all
```

`CLEAN` means no classified operational failure was observed; it does not mean the strategy is
profitable. The evidence counter uses 200 closed option trades as the minimum forward-test gate.
Old triggers created before structured option execution appear as `unclassified` rather than being
silently treated as missed trades.

Tables are:

- `watchlist_sessions`
- `market_feature_snapshots`
- `strategy_states`
- `state_transitions`
- `signals`
- `shadow_trades`
- `shadow_trade_outcomes`
- `shadow_option_trades`
- `shadow_option_marks`
- `shadow_execution_events`
- `application_events`

## Validation

```powershell
python -m pytest -q
ruff check .
mypy app
python -m app.main demo
python -m app.main market-check --stream-seconds 15
python -m app.main scorecard
```

## Known limitations and next phases

In priority order:

1. Accumulate a statistically meaningful sample across different market regimes; compare win
   rate, expected value, drawdown, setup features, entry timing, spread, Greeks, and time of day.
2. Complete multi-session reconnect/refresh soak testing against the verified read-only
   production grant.
3. Use the verified market-session endpoint in the runtime scheduler for holidays and half-days.
   The current session helper handles
   weekdays, Chicago time, and DST but not exchange holidays.
4. Add database migrations, retention, and outcome compaction for longer pilot runs.
5. Forward-score interesting candidates that never trigger to measure strategy-level missed
   opportunities; current missed-entry metrics cover triggered setups only.
6. Add verified sector ETF context; no sector mapping is guessed in this version.

Do not treat the current thresholds or synthetic results as evidence of profitability. Run shadow
mode through varied market regimes and analyze versioned outcomes before changing rules.
