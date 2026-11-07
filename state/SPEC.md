# state/ — Spec

## Files
- `state/window.py` — WindowState dataclass and phase logic
- `state/session.py` — BotState and trade recording

---

## state/window.py

### WindowPhase Enum

```python
class WindowPhase(Enum):
    WAITING       = "WAITING"        # window exists but too early to trade
    ENTRY_WINDOW  = "ENTRY_WINDOW"   # within 90s–15s of close — valid entry zone
    TOO_LATE      = "TOO_LATE"       # within 15s of close — do not enter
    EXPIRED       = "EXPIRED"         # window has closed
```

### WindowState Dataclass

```python
@dataclass
class WindowState:
    start_ts: int          # Unix timestamp: window opens (= end_ts - 300)
    end_ts: int            # Unix timestamp: window closes and resolves
    market_id: str         # Polymarket condition_id
    up_token_id: str       # token to buy if betting UP
    down_token_id: str     # token to buy if betting DOWN
    open_price: float = 0.0     # BTC price at window start (set on first observation)
    traded: bool = False        # True after we place (or skip) a trade this window
    order_id: Optional[str] = None  # filled in after a successful order
    market_url: str = ""        # https://polymarket.com/event/btc-updown-5m-{end_ts}
```

### Methods on WindowState

```python
def seconds_remaining(self, now: int) -> int:
    """Returns max(0, end_ts - now)."""

def get_phase(self, now: int) -> WindowPhase:
    """
    EXPIRED       if now >= end_ts
    TOO_LATE      if seconds_remaining < ENTRY_WINDOW_END
    ENTRY_WINDOW  if seconds_remaining <= ENTRY_WINDOW_START
    WAITING       otherwise
    """

def is_in_entry_window(self, now: int) -> bool:
    """True only when phase == ENTRY_WINDOW."""

def set_open_price(self, price: float) -> None:
    """Set open_price only if it hasn't been set yet (open_price == 0)."""

def market_url(self) -> str:
    """Return the Polymarket URL for this window, for logging."""
```

---

## state/session.py

### TradeRecord Dataclass

```python
@dataclass
class TradeRecord:
    ts: float              # time.time() when trade was placed
    window_end_ts: int
    direction: str         # "UP" or "DOWN"
    amount_usdc: float
    price: float           # price paid per share
    edge: float            # edge at time of trade
    model: str             # which probability model was used
    order_id: Optional[str]
    dry_run: bool
    resolved: bool = False
    resolved_up: Optional[bool] = None
    pnl: float = 0.0
```

### BotState Dataclass

```python
@dataclass
class BotState:
    btc_price: float = 0.0
    btc_price_updated_ts: float = 0.0    # time.time() of last price update
    last_trade_ts: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    current_window: Optional[WindowState] = None
    trade_history: list[TradeRecord] = field(default_factory=list)
```

### Methods on BotState

```python
def record_trade(
    self,
    direction: str,
    amount: float,
    price: float,
    edge: float,
    model: str,
    order_id: Optional[str],
    dry_run: bool,
    window_end_ts: int,
) -> TradeRecord:
    """Append a TradeRecord, increment counters, return the record."""

def record_resolution(self, order_id: str, resolved_up: bool) -> None:
    """
    Find trade by order_id. Calculate PnL:
      - If our direction matches resolution: pnl = amount * (1/price - 1)
      - Otherwise: pnl = -amount
    Update wins/losses/consecutive_losses/daily_pnl.
    """

def is_price_stale(self) -> bool:
    """True if btc_price_updated_ts is more than PRICE_STALE_THRESHOLD_SECONDS ago."""

def reset_daily(self) -> None:
    """Reset daily_pnl and daily_trades to 0. Call at midnight UTC."""
```
