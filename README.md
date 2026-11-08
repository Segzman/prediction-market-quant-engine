# Prediction Market Quant Engine

An event-contract pricing and execution engine in asyncio Python: it prices Polymarket's 5-minute BTC Up/Down contracts by comparing live multi-exchange spot prices with the contract prices the market is quoting, sizing bets by expected value, and defaulting to dry-run.

Built by Oluwasekunayo Faseyi (github.com/Segzman)

> **Research and paper-trading project.** Shadow/paper results so far cover about 105 resolved trades with no out-of-sample validation, so no claim of edge is made. Not financial advice. Live trading can lose money. Read the [Disclaimer](#disclaimer) first.

## Overview

Polymarket lists binary contracts that settle "Up" or "Down" for BTC over each fixed 5-minute window (`WINDOW_DURATION_SECONDS = 300`). Each contract pays 1 if its side wins and 0 otherwise, so its price is a probability the market assigns to that outcome.

The engine:

1. Streams BTC spot prices from several exchanges and picks a healthy quote (fastest or median).
2. Records the window's open price, then tracks the move from that price.
3. Converts the move and time remaining into a model probability `p`, optionally blended with the market's own implied probability.
4. Compares `p` with the contract price `q` to get an edge, and only trades when the edge, price band, liquidity, and expected-profit gates all pass.
5. Sizes the bet with a bankroll-aware rule (Kelly is available as an upper bound), and routes it through a dry-run or live order layer.
6. Records every submission, fill, cancellation, and resolution to an append-only JSONL ledger, which feeds the research and reporting scripts.

## Strategy and Models

All formulas below are taken from `strategy/` and `risk/`.

**Window and move.** At window start the open price `S0` is set from the latest BTC price. The move is `m = (S - S0) / S0`. A mispricing signal needs `|m| >= MOMENTUM_THRESHOLD` (default 0.05%).

**Probability models** (`strategy/probability.py`). Each returns `p`, the probability that BTC finishes above `S0`. Results are clamped to `[0.05, 0.95]`.

| Model | Idea | Formula (plain terms) |
|---|---|---|
| A, momentum | Linear confidence in the move, growing as the window runs down | `p = 0.5 ± 0.085 × (|m| / threshold) × (1 - t_remaining / 300)` |
| B, Brownian | Probability of staying on the same side under a random walk with recent volatility `σ` | `p = Φ( ln(S / S0) / (σ × √t_remaining) )`, plus a directional-bias adjustment in the late window. `Φ` comes from the standard library's `statistics.NormalDist`. |
| C, calibrated | Empirical hit rates from past trades, bucketed by move, time left, consistency, and acceleration | Bucket lookup from a calibration artifact built by `scripts/build_model_c_calibration.py`. Sparse buckets are blended toward a baseline by sample count. |
| P, pooled | Weighted blend of A, B, C, and the market-implied probability | Base weights A 0.9, B 1.05, C 1.2, market 0.55, shifted by time bucket and context. |

The market-implied probability is the normalized mid of the Up and Down books: `mkt_up = mid_up / (mid_up + mid_down)`.

**Edge and expected value** (`strategy/edge.py`).

- Edge for Up is `p - q`. Edge for Down is `(1 - p) - q`, where `q` is the contract price.
- A trade is tradeable when `edge >= EDGE_THRESHOLD` (default 0.06).
- Expected value per bet is `EV = stake × edge / q`. This is the expected profit on a stake that buys `stake / q` contracts.

**Two entry regimes** (`strategy/signal.py`).

- *Mispricing.* The model says BTC has moved, but the contract has not repriced. It fires only when `q` is inside the entry band (default 0.50 to 0.62).
- *Late conviction.* Late in the window, the engine scores each side of the Polymarket book. `market_conviction = min(1, 0.72 × strongest-side price + up to 0.18 momentum + up to 0.08 acceleration + 0.12 × (1 - one-sidedness) + queue-imbalance and microprice terms)`. It enters the dominant side only when BTC and the book agree (`btc_poly_agreement`), inside its own price band and expected-profit floor.

Tracks select which regimes run: `CONVICTION_ONLY` (default), `MISPRICING_ONLY`, or `BOTH`. Shadow runs can execute all tracks side by side, each writing its own ledger.

**Hedging.** An optional opposite-side leg (`HEDGE_FRACTION`, default 0.5) can be placed after a mispricing entry, with a cooldown and a loss cap. Late-conviction entries are not hedged unless explicitly allowed.

**Optional second opinion.** `strategy/jev.py` can ask an external model to confirm BTC direction for contested or expensive entries. It is off by default and fails closed: any timeout or error keeps the deterministic decision.

**Sizing** (`risk/sizing.py`).

- Adaptive rule: `bet = bankroll × BANKROLL_RISK_FRACTION (0.25) × bankroll_factor × streak_factor × edge_factor × tier × regime × exposure factors`. Bankroll, streak, and exposure factors are all clamped. The result is clamped to `[MIN_BET_USDC, min(MAX_BET_USDC, bankroll)]`, defaults `$2` to `$5`.
- Kelly (`USE_KELLY`, default off): `f* = (b·p - (1 - p)) / b` with `b = (1 - q) / q`, scaled by `KELLY_FRACTION` (0.5). When enabled it is an upper bound on the adaptive bet, not a replacement for it.

**Exits.** Take-profit tiers (`EXIT_TP1_PCT`, `EXIT_TAKE_PROFIT_PCT`), stop-loss with confirmation and momentum-aware suppression, velocity stop, time exits by regime, and reverse-signal exit. Parameters live in `config.py`.

## Architecture

```
codex_plan/
├── bot.py               # CLI entry point and asyncio runtime loop
├── config.py            # env loading, constants, validation
├── feeds/               # BTC spot feeds: Binance and Coinbase (binance.py), Kraken, Gemini, Bybit
├── market/              # Polymarket market finder, public and authenticated client, orderer, Kalshi reference
├── strategy/            # probability models, edge/EV, signal evaluation, entry gates, optional JEV check
├── risk/                # sizing (adaptive, Kelly), DailyGuard
├── state/               # window, position, session state, model tracks
├── persistence/         # append-only JSONL ledger
├── ops/                 # process lock, alerts, live preflight and reconciliation, resolution
├── webapp/              # local control plane (HTTP server + run manager)
├── scripts/             # replay, sweep, tuning, calibration, reporting
├── tests/               # pytest suite
└── docs/                # build spec, runbooks, sign-off notes
```

Data flow:

- Exchange WebSockets and REST feeds update the shared BTC state.
- Polymarket finder resolves the active 5-minute market and its token IDs. The client pulls order books.
- Each tick: `window` state and feature snapshot go to `strategy.signal`, which returns a signal or a reason code.
- `risk.guard` checks limits, `risk.sizing` picks the stake, and `market.orderer` places the order (dry-run logs only).
- Fills, cancels, and resolutions are appended to the ledger and mirrored to `runtime_status.json` and `alerts.jsonl`.

Concurrency: feed tasks and the trading loop run under `asyncio.gather`. The decision code itself is synchronous.

## Risk Controls

- **Dry-run by default.** `DRY_RUN` defaults to `True`. The orderer checks the flag before any order call. `--live` also requires `--yes-live`.
- **Entry price band.** Entries outside `ENTRY_MIN_CONTRACT_PRICE` to `ENTRY_MAX_CONTRACT_PRICE` are rejected.
- **Liquidity gate.** Minimum ask and bid counts, a maximum side spread, and a minimum combined liquidity score.
- **Expected-profit gate.** Net expected profit after estimated taker fees and a slippage buffer (`EXPECTED_SLIPPAGE_BPS`) must clear a minimum.
- **Session limits** (`risk/guard.py`). Daily loss limit, drawdown limit, max trades per day, max unresolved and per-window exposure, max positions per window, and a pause after consecutive losses.
- **Stale and unhealthy data.** Entries are blocked on a stale BTC price. A minimum number of healthy feeds and a maximum cross-feed divergence are required.
- **Single-process lock.** `ops/lock.py` takes an exclusive `fcntl` lock on `BOT_LOCK_FILE`.
- **Live preflight.** Balance, allowance, and open-order checks at startup, with `LIVE_MIN_AVAILABLE_USDC` and `LIVE_BALANCE_BUFFER_USDC` margins.
- **Startup reconciliation.** Authenticated trade history is compared with the ledger over `LIVE_RECON_LOOKBACK_SECONDS`. Untracked recent fills are reported, and open order intents are reconciled or aborted after a grace period.
- **Passive live orders.** Live entries are GTC limit orders with a slippage cap (`LIVE_ORDER_SLIPPAGE_BPS`).
- **Kill switch.** If `LIVE_KILL_SWITCH_FILE` exists, live trading stops.
- **Alerts.** Structured events go to `alerts.jsonl`, with an optional webhook (`ALERT_WEBHOOK_URL`). Order-failure and risk-breach alerts are throttled.
- **Ledger as source of truth.** Trades are recorded as submission, fill, cancellation, and resolution records. Restarts recover unresolved trades from it.

## Research Tooling

- **Simulators.** `research/simulate_day.py` (Monte Carlo day from trade-data calibration), `research/daily_simulation.py` (win-rate scenarios over 288 windows), `research/full_day_volatile_sim.py` (volatile BTC paths run through the real signal engine), `research/simulate_hedge_strategies.py` (hedge variants), `scripts/simulate_models.py` (multi-session model comparison), `scripts/sweep_exits.py` (exit-parameter grid on synthetic windows).
- **Replay and tuning.** `scripts/replay_conviction.py` replays conviction configs against recorded candidates with no lookahead. `scripts/batch_tune_test.py` tunes on older windows and tests on newer ones, backfilling outcomes from Binance 1-minute closes.
- **Market history.** `scripts/sync_history.py` keeps a local cache of BTC 1-minute history, and `scripts/day_history.py` scores momentum direction accuracy per window offset.
- **Calibration.** `scripts/build_model_c_calibration.py` builds the Model C artifact from the ledger.
- **Performance reports.** `scripts/report_performance.py` summarizes each model's gross PnL, estimated fees (crypto taker fee model), net PnL, and submit and fill slippage. `scripts/summarize_active_run.py` produces an operational health summary for a running session.
- **Analysis.** `research/analyze_entry_band.py`, `research/analyze_sl.py`, `research/analyze_momentum_sl.py`, `research/analyze_option1.py`, and `research/analyze_performance.py` analyze entry bands, stop-loss variants, and trade logs.
- **Local control plane.** `python -m webapp.server` serves a dashboard on `127.0.0.1:8787` with start and stop controls, log tailing, the runtime heartbeat, and per-model reports. Each run gets its own log and ledger.

## Evaluation

[`docs/backtest_report.md`](docs/backtest_report.md) is generated by `research/backtest_report.py` from `research/data/sample_trades.jsonl`, an anonymized sample of two logged runs. It scores the bot's probability models, the market-implied baseline, and the optional JEV check (`research/jev_replay.py`) on a chronological test split, with Brier, log loss, reliability, Wilson-CI hit rates, fee-net trade EV, max drawdown and a window-clustered bootstrap.

Sample size is small: 65 windows with a usable outcome proxy, 20 of them in the test split, and 28 test-set trades. Outcomes are a proxy (last observed BTC price against the window open), not contract settlement, and the logged Models B and C are identical. Treat the report as a reproducible method and a first look, not evidence of edge.

```bash
python research/backtest_report.py --build-sample <dir with trades_both.jsonl and trades_conviction.jsonl>
python research/backtest_report.py   # writes docs/backtest_report.md
```

## Testing

- 24 test files and 292 test functions (as reported by `pytest -q`), covering the probability models, edge, signal, sizing, guard, window and position state, ledger, orderer, feeds, Polymarket client, live reconciliation, webapp manager, and calibration script.
- Run the suite with `python -m pytest -q`. It uses `pytest-asyncio` and `unittest.mock` for external services, so it needs no network access or credentials.
- CI (`.github/workflows/ci.yml`) runs the suite on Python 3.13 with the pinned `requirements.lock`.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # keep DRY_RUN=True; fill in keys only for --live
python bot.py                 # shadow mode: real public data, dry-run orders
```

Other modes:

```bash
python bot.py --demo-local --models A,B,C --duration 120   # local simulator, several models
python bot.py --shadow-live --asset ETH --dashboard        # other assets: ETH, SOL
python -m webapp.server                                    # dashboard at http://127.0.0.1:8787
python -m pytest -q                                        # test suite
```

## Tech Stack

- Python 3, `asyncio`, `websockets` (exchange feeds), `py-clob-client` (Polymarket authenticated trading), `python-dotenv` (configuration)
- Standard library for statistics (`statistics.NormalDist`), HTTP (`urllib`), the local web server (`http.server`), and process locking (`fcntl`)
- `pytest`, `pytest-asyncio`, `unittest.mock`
- JSONL ledgers, JSON calibration and runtime snapshots

## Repository Notes

- `docs/dev-notes/BUILD_SPEC.md` is the original build specification the codebase was written against. Some of its claims are out of date; the code is authoritative.
- Local runtime data (trade ledgers, cached price history, status snapshots, alerts) and secrets (`.env`) are git-ignored and are not part of this repository.

## Known Limitations

- **Sample size.** Paper/shadow ledgers hold roughly 105 resolved trades, too few to establish an edge.
- **No out-of-sample split yet.** Calibration buckets for Model C are scored on the same resolutions they are fit on; a chronological train/test split is the next step.
- **Look-ahead risk in replay.** Model C's ledger fallback can read resolutions later than the decision being replayed; it should filter to windows that ended before the decision time.
- **Synthetic market in `scripts/simulate_models.py`.** The simulated order book under-reacts to momentum by construction, so it tests plumbing, not edge.
- **Execution model.** No queue-position, partial-fill, or latency/slippage sensitivity model.
- **Shadow run outcome.** `docs/shadow_no_go_2026-05-12.md` records a NO-GO decision on feed-stability evidence; the engine fails closed rather than trading through degraded feeds.

## Disclaimer

This software is provided for research and educational purposes only. It is not financial advice, and nothing here is a recommendation to buy or sell any contract. Prediction markets carry real risk of loss, including total loss of stake. Backtests, simulations, and dry-run results do not guarantee live performance. Live trading depends on exchange and network behavior, fees, liquidity, and your own judgement. Use `--live` only with money you can afford to lose, and only after reviewing the code and the risk controls above.
