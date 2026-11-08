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

**Optional second opinion.** `strategy/jev.py` can ask an external AI model to confirm BTC direction on contested or expensive entries. Off by default and fail-closed; see [Experimenting with JEV](#experimenting-with-jev-an-ai-judgment-api).

**Sizing** (`risk/sizing.py`).

- Adaptive rule: `bet = bankroll × BANKROLL_RISK_FRACTION (0.25) × bankroll_factor × streak_factor × edge_factor × tier × regime × exposure factors`. Bankroll, streak, and exposure factors are all clamped. The result is clamped to `[MIN_BET_USDC, min(MAX_BET_USDC, bankroll)]`, defaults `$2` to `$5`.
- Kelly (`USE_KELLY`, default off): `f* = (b·p - (1 - p)) / b` with `b = (1 - q) / q`, scaled by `KELLY_FRACTION` (0.5). When enabled it is an upper bound on the adaptive bet, not a replacement for it.

**Exits.** Take-profit tiers (`EXIT_TP1_PCT`, `EXIT_TAKE_PROFIT_PCT`), stop-loss with confirmation and momentum-aware suppression, velocity stop, time exits by regime, and reverse-signal exit. Parameters live in `config.py`.

## Experimenting with JEV (an AI judgment API)

I wanted to try a newer kind of AI tooling: APIs that return a **typed judgment** about structured data instead of free-form chat text. JEV (TypeSafe's System One API, model `jev-latest`) takes a JSON `state` plus a named question with an answer type, and returns a typed answer, here a probability.

The idea was simple: instead of running the whole model stack, gather a handful of the same numbers the engine already computes (momentum, tick direction, consistency, acceleration, contract price movement), hand them to an API, and ask one question, essentially "should this entry go ahead?" Doing that on every contested decision turns the bot into a small experiment with four questions:

- **Question framing.** Does the way a question is worded change the answer? The prompt deliberately asks about directional agreement, not "will it settle Up", to see whether a narrowly framed question gives more useful answers.
- **Do the decision models hold their weight?** If a non-deterministic model fed a few deterministic inputs agrees or disagrees with the calibrated models, that is a cheap stress test of whether the hand-built weights add anything.
- **Latency.** Is one API round trip on a few variables competitive with computing everything locally inside a 5-minute window, where seconds matter?
- **Cost.** Is paying per question for a handful of contested calls cheaper than keeping a VM running all the models all the time?

**How it works** (`strategy/jev.py`, called from `strategy/signal.py`):

1. The deterministic signal path computes the market's strongest side and a BTC/contract agreement check from tick move, directional consistency, acceleration, and contract price momentum.
2. `resolve_btc_confirmation` only spends an API call when the answer could change the outcome: the deterministic check disagrees, or the entry is expensive (price above the late-conviction threshold). Cheap, uncontested entries never call out.
3. `query_btc_agreement` sends those features as the `state` with one question, `btc_poly_agreement`: "does observed BTC movement corroborate the strongest side?" It asks for directional agreement, not a settlement probability.
4. The returned probability confirms the entry only if it is at least `JEV_NOUL_MIN_PROBABILITY` (default 0.60).

**Guardrails, because it is an external dependency in a latency-sensitive loop:**

- **Off by default** (`JEV_NOUL_ENABLED=False`) and inert without `TYPESAFE_API_KEY`.
- **Fail-closed:** any timeout (5 s default), transport error, or malformed answer returns `None`, and the engine keeps its deterministic decision.
- **Cost and latency control:** a 15 second TTL cache keyed on bucketed inputs (time remaining in 5 s steps, rounded features), capped at 256 entries.
- **Answer parsing** accepts numeric or boolean answers and clamps to [0, 1].
- **Tests:** `tests/test_jev.py` covers the gating, the cache, and the fail-closed paths with the network mocked. `tests/test_jev_log.py` covers the decision log and `tests/test_jev_report.py` covers report aggregation.

**Status:** the plumbing is built and tested, and every `resolve_btc_confirmation` decision now appends one JSON line to `JEV_LOG_FILE` (default `jev_calls.jsonl`, gitignored). Each line records the window, features, whether a call was made or skipped and why, cache hits, HTTP latency, returned probability, error class, both decisions, and an estimated cost from `JEV_COST_PER_CALL_USD` (default 0.0, meaning unknown). Logging is fail-safe: write errors are swallowed and never change the decision. Build the report with `python -m research.jev_report`, optionally adding `--ledger trades.jsonl` to join window outcomes and `--out docs/jev_report.md` to choose the output path. It prints and writes calls made vs skipped by reason, cache hit rate, latency p50/p95/max, error rate, JEV versus deterministic agreement, decisions changed by JEV, estimated total and per-session cost, and outcome accuracy with sample sizes plus a small-sample warning. The four questions above are still open: numbers are pending real runs. Whether JEV improves decisions is not established and it has not been evaluated out of sample, so it stays an experiment, separate from the core models (A, B, C, P), which are my own. The lesson so far: the integration pattern (gate, cache, fail closed, threshold) matters more than the model.

**Question-framing experiment:** [research/jev_framing/README.md](research/jev_framing/README.md). On 139 holdout states (18 windows), a neutral wording cut Brier from 0.217 to 0.189 vs the production wording, but the 95% interval includes zero. Calls only, no billing data.

## Architecture

```
prediction-market-quant-engine/
├── bot.py               # entry point, asyncio runtime loop, trading orchestration
├── cli.py               # argument parsing and model-name validation
├── config.py            # env loading, constants, validation
├── feeds/               # BTC spot feeds: Binance and Coinbase (binance.py), Kraken, Gemini, Bybit
├── market/              # Polymarket market finder, public and authenticated client, orderer, Kalshi reference, window slug parsing (slug.py)
├── strategy/            # probability models, edge/EV, signal evaluation, entry gates, optional JEV check
├── risk/                # sizing (adaptive, Kelly), DailyGuard
├── state/               # window, position, session state (incl. apply_price), model tracks
├── persistence/         # append-only JSONL ledger
├── ops/                 # process lock, alerts, live preflight and reconciliation, resolution, text formatting (formatting.py), runtime dashboard and status file (dashboard.py)
├── sim/                 # local demo market client (DemoClient) and simulated BTC feed (SimulatedFeed)
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

**Month-long backtest with real settlement.** [`docs/month_backtest.md`](docs/month_backtest.md) (generated by `research/month_backtest/run.py`) rebuilds 33,763 decision states over 30 days (2026-10-08 to 2026-11-06, 8,544 settled BTC 5-minute windows) from Binance 1-second prices and CLOB quote history, and scores them against Polymarket's settled outcomes. The market price wins: its Brier is 0.079, against 0.083 for the best model (P), and every model's paired difference has a 95% CI above zero. JEV, called on 30,012 states (p50 latency 170 ms), scored 0.402, worse than a coin flip. The simplified trading sim shows positive PnL, but the report treats that as a stale-quote artifact rather than an edge.

```bash
python research/backtest_report.py --build-sample <dir with trades_both.jsonl and trades_conviction.jsonl>
python research/backtest_report.py   # writes docs/backtest_report.md
```

## Paper Deployment

`deploy/vm/` runs the engine 24/7 in paper mode on a Linux VM: an idempotent `bootstrap.sh`, systemd units for the bot and a read-only dashboard (`/healthz`), a heal timer that reinstalls after a reprovision, and a log-sync timer that backs up the paper ledger and JEV logs to a private repository and restores them on bootstrap. It refuses to start unless `DRY_RUN=True`. See [deploy/vm/README.md](deploy/vm/README.md).

## Testing

- 28 test files and 321 tests (as reported by `pytest -q`), covering the probability models, edge, signal, sizing, guard, window and position state, ledger, orderer, feeds, Polymarket client, live reconciliation, webapp manager, and calibration script.
- Run the suite with `python -m pytest -q`. It uses `pytest-asyncio` and `unittest.mock` for external services, so it needs no network access or credentials.
- CI (`.github/workflows/ci.yml`) runs the suite on Python 3.13 with the pinned `requirements.lock`.

## Development

Requires Python 3.11 or newer. Install the runtime and development dependencies:

```bash
pip install -r requirements.txt
pip install ruff mypy
```

Run the checks from the repository root:

```bash
ruff check .        # lint
mypy                # type checks for strategy/, risk/, persistence/
python -m pytest -q # test suite
```

Ruff is configured in `pyproject.toml` with rules E, F, I, B, and UP at line length 110. mypy uses the same file, with `disallow_untyped_defs` enabled for `strategy/`, `risk/`, and `persistence/`.

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
- **Out-of-sample evaluation is still thin.** `docs/backtest_report.md` uses a chronological split, but the test side is about 20 independent windows and outcomes are a proxy (last observed BTC price vs window open) because the logs carry no settlement result. Models B, C and P beat the market-implied baseline on Brier there (about 0.15 vs 0.17), but the month-long backtest against real settlement (`docs/month_backtest.md`, 33,763 states) reverses that: the market wins on Brier. No edge is claimed.
- **Model C fell back to Model B in the logged runs.** B and C are identical on all 8,125 logged rows, so the calibration path never engaged in those sessions; the paper deployment logs settlement outcomes to fix that.
- **Look-ahead in Model C ledger fallback: fixed.** The fallback calibration now passes the decision timestamp through the call chain (`strategy/signal.py` derives it as `window.end_ts - seconds_remaining`) and counts only resolutions whose `window_end_ts` is strictly before that time. Ledgers are parsed once and cached keyed on file mtime, so every Model C tick no longer re-reads the JSONL file.
- **Synthetic market in `scripts/simulate_models.py`.** The simulated order book under-reacts to momentum by construction, so it tests plumbing, not edge.
- **Execution model.** No queue-position, partial-fill, or latency/slippage sensitivity model.
- **Shadow run outcome.** `docs/shadow_no_go_2026-05-12.md` records a NO-GO decision on feed-stability evidence; the engine fails closed rather than trading through degraded feeds.

## Disclaimer

This software is provided for research and educational purposes only. It is not financial advice, and nothing here is a recommendation to buy or sell any contract. Prediction markets carry real risk of loss, including total loss of stake. Backtests, simulations, and dry-run results do not guarantee live performance. Live trading depends on exchange and network behavior, fees, liquidity, and your own judgement. Use `--live` only with money you can afford to lose, and only after reviewing the code and the risk controls above.
