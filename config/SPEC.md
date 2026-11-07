# config.py — Spec

## Responsibilities

Single source of truth for all constants and environment variables.
No other module should call `os.getenv` directly — everything comes from here.

## Environment Variables

Load from `.env` via `python-dotenv`. Validate on import — raise `ValueError` with
a clear message if a required variable is missing.

```python
# Required
POLYMARKET_PRIVATE_KEY: str    # 0x... Polygon wallet private key
POLYMARKET_API_KEY: str        # from Polymarket profile → API keys
POLYMARKET_SECRET: str
POLYMARKET_PASSPHRASE: str

# Optional with defaults
DRY_RUN: bool = True           # ALWAYS default True — user must opt into live trading
LOG_LEVEL: str = "INFO"
LEDGER_FILE: str = "trades.jsonl"
```

## Strategy Constants

```python
# Signal thresholds
MOMENTUM_THRESHOLD: float = 0.0005   # 0.05% BTC move to trigger signal evaluation
EDGE_THRESHOLD: float = 0.04         # 4¢ minimum edge to place a trade

# Entry timing (seconds before window close)
ENTRY_WINDOW_START: int = 90         # start watching for signal
ENTRY_WINDOW_END: int = 15           # stop entering (too close to expiry)

# Risk
MAX_BET_USDC: float = 5.0            # max $ per trade
MAX_DAILY_LOSS_USDC: float = 20.0    # daily stop-loss
MAX_TRADES_PER_DAY: int = 50         # sanity cap
COOLDOWN_SECONDS: int = 60           # min time between trades
CONSECUTIVE_LOSS_PAUSE_SECONDS: int = 600   # pause after 3 losses in a row
CONSECUTIVE_LOSS_LIMIT: int = 3

# Sizing
USE_KELLY: bool = False              # default to fixed fraction; Kelly is optional
KELLY_FRACTION: float = 0.5          # half-Kelly

# Feed
BINANCE_WS_URL: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
PRICE_STALE_THRESHOLD_SECONDS: int = 5   # warn if no BTC update in this long

# Polymarket
POLYMARKET_HOST: str = "https://clob.polymarket.com"
MARKET_CACHE_TTL_SECONDS: int = 60   # how long to cache found window before re-querying
```

## `.env.example`

```env
# Required
POLYMARKET_PRIVATE_KEY=0xyour_key_here
POLYMARKET_API_KEY=your_api_key
POLYMARKET_SECRET=your_secret
POLYMARKET_PASSPHRASE=your_passphrase

# Set to False only after validating strategy in dry run
DRY_RUN=True

# Optional
LOG_LEVEL=INFO
LEDGER_FILE=trades.jsonl
USE_KELLY=False
```

## Validation Function

```python
def validate_config() -> None:
    """Call once at startup. Raises ValueError if anything is wrong."""
    ...
```

Check:
- Required keys are non-empty strings
- `MAX_BET_USDC > 0`
- `EDGE_THRESHOLD > 0 and < 1`
- `ENTRY_WINDOW_START > ENTRY_WINDOW_END`
- `KELLY_FRACTION > 0 and <= 1`
- If `DRY_RUN=False`, log a prominent warning: "LIVE TRADING MODE — real money at risk"
