# Polymarket BTC 5-Min Bot — Codex Build Instructions

## What This Is

A Python trading bot that trades Polymarket's BTC Up/Down 5-minute prediction markets.
Each window lasts 5 minutes. The bot watches BTC price via Binance WebSocket, detects
momentum in the last 90 seconds of a window, compares it to Polymarket's implied odds,
and bets when the market hasn't priced in the move yet.

## How to Read These Files

Each folder has a `SPEC.md` file. Read them in this order:

1. `README.md` (this file)
2. `ARCHITECTURE.md` — full directory layout and data flow
3. `state/SPEC.md` — core data structures, read this before anything else
4. `config/SPEC.md` — env vars and constants
5. `feeds/SPEC.md` — BTC price feed
6. `market/SPEC.md` — Polymarket API wrapper
7. `strategy/SPEC.md` — signal generation and probability models
8. `risk/SPEC.md` — position sizing and guards
9. `persistence/SPEC.md` — trade ledger
10. `tests/SPEC.md` — all test cases

## Implementation Order

Implement in this exact order so each layer is testable before building the next:

1. `config.py`
2. `state/window.py` + `state/session.py`
3. `persistence/ledger.py`
4. `strategy/probability.py` + `strategy/edge.py` + `strategy/signal.py`
5. `risk/sizing.py` + `risk/guard.py`
6. `feeds/binance.py`
7. `market/finder.py` + `market/client.py` + `market/orderer.py`
8. `bot.py` (orchestrator — wire everything together last)

Write and pass tests for each module before moving to the next.

## Non-Negotiables

- `DRY_RUN=True` by default. No real orders until the user explicitly sets `DRY_RUN=False` in `.env`
- Every external API call must be mockable (no hardcoded HTTP calls in business logic)
- All modules independently testable — no god objects
- Use `asyncio` throughout
- `pytest` for all tests with `unittest.mock` for external dependencies

## Stack

- Python 3.11+
- `py-clob-client` — Polymarket CLOB API
- `websockets` — Binance price feed
- `python-dotenv` — env config
- `pytest` + `pytest-asyncio` — tests
- `scipy` — for Brownian motion probability model (Model B)

## Quick Start (after Codex builds it)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in keys only if you plan to use --live
python bot.py                    # SHADOW LIVE: real Binance + public Polymarket data, dry-run orders
python bot.py --shadow-live      # explicit alias for the default mode above
python bot.py --shadow-live --asset ETH
python bot.py --shadow-live --asset SOL
python bot.py --demo-local --models A,B,C --duration 120
python bot.py --shadow-live --duration 300 --dashboard
python bot.py --demo --duration 60
python bot.py --demo-local --duration 60
python bot.py --live             # real money — requires confirmation and live credentials
python -m webapp.server          # local web control plane at http://127.0.0.1:8787
python scripts/build_model_c_calibration.py --ledger trades.jsonl --output model_c_calibration.json
python scripts/report_performance.py --ledger trades.jsonl
python -m pytest       # run all tests
```

## Production Readiness

Before using `--live`, work through [PROD_READY_CHECKLIST.md](PROD_READY_CHECKLIST.md). It breaks production hardening into parallel workstreams for strategy, execution, risk, operations, and security.
The runtime now includes a first production-hardening pass for live mode: account preflight, live bankroll capping from real available funds, passive limit order posting with a slippage cap, startup stale-order handling, startup ledger reconciliation for unresolved prior trades, richer trade feature snapshots, and a file-based kill switch.
It also writes a machine-readable runtime snapshot to `runtime_status.json` by default so shadow-live and live runs can be supervised outside the terminal dashboard.
For local operations, the repo now includes a browser control plane that starts and stops managed bot runs, tails logs, renders the JSON heartbeat, and shows per-model performance from the run ledger.
The runtime now also enforces a single-process lock, writes structured alerts to `alerts.jsonl`, and uses authenticated trade history during live startup reconciliation so restarts can detect untracked fills instead of trusting only the local ledger.

## Web Dashboard

- `python -m webapp.server`
  Starts a local dashboard at [http://127.0.0.1:8787](http://127.0.0.1:8787).
- The page can start `shadow-live`, `demo`, `demo-local`, and `live` runs.
- Each web-started run gets its own log, ledger, and status files under [.web_runtime](.web_runtime).
- Live runs launched from the web use the explicit `--yes-live` confirmation flag, so keep the dashboard bound to localhost and do not expose it publicly.

## Runtime Modes

- `python bot.py`
  Uses real Binance price data and real public Polymarket market/order-book data, but keeps order placement in dry-run mode. On interactive terminal runs, a live dashboard is shown by default. By default the runtime does not force an interval trade when quality gates fail, and it rejects extreme-priced contracts outside the configurable entry band before placing a primary or hedge leg.
- `--dashboard` / `--no-dashboard`
  Enables or disables the live terminal table showing runtime, market state, latest signal, order status, and session stats.
- `ENTRY_MIN_CONTRACT_PRICE` / `ENTRY_MAX_CONTRACT_PRICE`
  Clamp entries to a sane price band. The default `0.55` to `0.62` range is meant to keep the bot out of ultra-cheap contracts and late expensive chases that dominated recent losing shadow-live runs.
- `EARLY_ENTRY_START_SECONDS` / `SNIPE_START_SECONDS` / `SNIPE_HARD_DEADLINE_SECONDS`
  Control adaptive timing. The bot now starts watching the book earlier, but only fires before the main snipe window when liquidity and expected net profit are materially better than the minimum thresholds.
- `LIQUIDITY_MIN_ASK_COUNT` / `LIQUIDITY_MIN_BID_COUNT` / `LIQUIDITY_MAX_SIDE_SPREAD` / `LIQUIDITY_MIN_SCORE`
  Control the liquidity gate. A window is now skipped when the book is one-sided, too thin, too wide, or simply too weak on the combined liquidity score.
- `MIN_EXPECTED_PROFIT_USDC` / `EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC` / `EXPECTED_SLIPPAGE_BPS`
  Control the net-profit gate. The bot now estimates expected PnL after taker fees and a spread/slippage buffer before it will enter.
- Position sizing now compounds more conservatively in weak setups and more aggressively in strong ones. The tiered sizing path uses bankroll growth and drawdown as before, but it now also downweights thin-liquidity and low-net-profit setups instead of sizing only from raw edge.
- `RUNTIME_STATUS_FILE` / `RUNTIME_STATUS_UPDATE_SECONDS`
  Control the JSON heartbeat written during runtime. By default the bot updates `runtime_status.json` every 2 seconds with market state, per-model stats, bankroll, and the latest event.
- `BOT_LOCK_FILE`
  Prevents two bot processes from running against the same environment at once.
- `ALERTS_FILE` / `ALERT_WEBHOOK_URL`
  Control the structured alert sink. Alerts are always appended locally, and can optionally be mirrored to a webhook for live operations.
- `LIVE_RECON_LOOKBACK_SECONDS`
  Controls how far back authenticated trade history is scanned during live startup reconciliation. The bot now aborts if it detects recent live trades that do not exist in the ledger.
- `--models <list>`
  Runs multiple models side by side against the same price stream and market windows, for example `--models A,B,C`. This is supported in dry-run and demo modes, not `--live`.
- `--asset <symbol>`
  Selects the recurring 5-minute asset series to trade. Verified live on April 2, 2026 for `BTC`, `ETH`, and `SOL`. Scaffolding is also in place for `XRP` and `POL`, though `POL` still depends on the public series being exposed consistently.
- `python bot.py --demo`
  Runs a bounded dry-run demo that prefers live public data and falls back to local simulation when no suitable live 5-minute market is available.
- `python bot.py --demo-local`
  Forces the fast local simulator so you can watch signals, entries, cooldowns, hedges, and running stats without waiting on a real market window.
- `python bot.py --live`
  Uses authenticated Polymarket trading endpoints and can place real orders. This requires `POLYMARKET_PRIVATE_KEY` and either:
  - `POLYMARKET_API_KEY`, `POLYMARKET_SECRET`, `POLYMARKET_PASSPHRASE`
  - or just `POLYMARKET_PRIVATE_KEY`, in which case the client derives API credentials automatically.
  Live mode now defaults to the safer profile: `LIVE_FORCE_INTERVAL_TRADES=false`, `LIVE_HEDGE_ENABLED=false`, startup balance/allowance/open-order preflight, and passive `GTC` limit posting with `LIVE_ORDER_SLIPPAGE_BPS`.
  Optional live safety env vars: `LIVE_KILL_SWITCH_FILE`, `LIVE_STARTUP_CANCEL_ALL`, `LIVE_REQUIRE_EMPTY_OPEN_ORDERS`, `LIVE_MIN_AVAILABLE_USDC`, and `LIVE_BALANCE_BUFFER_USDC`.
- `python scripts/build_model_c_calibration.py`
  Builds a reusable Model C calibration artifact from the trade ledger so Model C can consume explicit bucket probabilities instead of relying only on raw JSONL fallback.
- `python scripts/report_performance.py`
  Summarizes ledger performance by model with gross PnL, estimated fees, net PnL, and average submit/fill slippage so you can score paper runs before risking capital.
  The runtime and dashboard also now expose no-trade reasons, liquidity score, and net expected profit so you can tell whether a skipped window was caused by missing book quality, low expected edge after costs, or a weak model signal.
