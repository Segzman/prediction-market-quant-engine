# Architecture

## Directory Layout

```
bot/
├── bot.py                     # entry point: asyncio orchestrator and trading loop
├── cli.py                     # CLI argument parsing and model-name validation
├── sim/
│   ├── __init__.py
│   ├── demo.py                # DemoClient: local demo market client
│   └── feed.py                # SimulatedFeed: local simulated BTC price feed
├── config.py                  # all constants + env loading + validation
├── feeds/
│   ├── __init__.py
│   ├── binance.py             # BTC price WebSocket (Binance trade stream)
│   └── chainlink.py           # optional: poll actual resolution source
├── market/
│   ├── __init__.py
│   ├── client.py              # thin wrapper around py-clob-client
│   ├── finder.py              # find active BTC 5-min windows from API
│   ├── orderer.py             # place / cancel / poll orders
│   └── slug.py                # extract_market_slug: window slug from market URL
├── strategy/
│   ├── __init__.py
│   ├── signal.py              # top-level: evaluate() → Signal | None
│   ├── probability.py         # Model A, B, C probability estimators
│   └── edge.py                # edge calc, EV, tradeability check
├── risk/
│   ├── __init__.py
│   ├── guard.py               # DailyGuard, consecutive loss pause
│   └── sizing.py              # fixed fraction + Kelly criterion
├── state/
│   ├── __init__.py
│   ├── window.py              # WindowState dataclass + phase logic
│   └── session.py             # BotState + trade recording
├── ops/
│   ├── formatting.py          # text formatting helpers (price, prob, runtime, age, fit_text)
│   └── dashboard.py           # RuntimeStats, ModelRuntime, terminal dashboard, runtime status file
├── persistence/
│   ├── __init__.py
│   └── ledger.py              # append-only JSONL trade log
├── tests/
│   ├── conftest.py            # shared fixtures
│   ├── test_probability.py
│   ├── test_edge.py
│   ├── test_signal.py
│   ├── test_risk.py
│   ├── test_window.py
│   ├── test_orderer.py
│   ├── test_ledger.py
│   └── test_finder.py
├── requirements.txt
├── .env.example
└── README.md
```

## Data Flow

```
Binance WS ──────────────────────────────────────────────────────────┐
  (btc_price updates BotState every tick)                            │
                                                                     ▼
Polymarket API ──► finder.py ──► WindowState ──► signal.py ──► guard.py
  (find active      (every 60s    (open_price,    (theo_prob   (can we
   5-min window)     or on expiry) end_ts, tokens)  vs mkt_price  trade?)
                                                     → Signal)
                                                          │
                                               YES, edge >= threshold
                                                          │
                                                          ▼
                                                    sizing.py
                                                 (how much to bet)
                                                          │
                                                          ▼
                                                    orderer.py
                                                 (place limit order)
                                                    │         │
                                               DRY_RUN     LIVE
                                               (log only)  (submit)
                                                          │
                                                          ▼
                                                    ledger.py
                                                 (record trade)
                                                          │
                                              (window closes)
                                                          │
                                                          ▼
                                                 record_resolution()
                                                 (update PnL, wins/losses)
```

## Concurrency Model

Two async tasks run forever in parallel via `asyncio.gather`:

1. `btc_price_feed(state)` — updates `state.btc_price` on every trade tick
2. `trading_loop(state, poly)` — evaluates signals and places orders

They share `BotState` (no locks needed — Python's GIL covers simple attribute reads/writes).

## External Dependencies

| Service | Usage | Library |
|---|---|---|
| Binance | BTC price feed | `websockets` (raw WS) |
| Polymarket CLOB | Find markets, place orders | `py-clob-client` |
| Chainlink | Optional: resolution price verification | HTTP polling |

## Key Design Constraints

- **No logic in `bot.py`** — it only wires modules together and calls `asyncio.gather`
- **No direct HTTP calls in strategy/risk/state** — all external I/O goes through `feeds/` or `market/`
- **All business logic is sync** — only feed and order I/O functions are async
- **DRY_RUN is checked in `orderer.py` only** — never scattered across the codebase
