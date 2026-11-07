# risk/ — Spec

## Files
- `risk/guard.py` — DailyGuard: checks whether trading is allowed
- `risk/sizing.py` — position sizing: fixed fraction and Kelly criterion

---

## risk/guard.py

### Class: DailyGuard

```python
class DailyGuard:
    def __init__(self, state: BotState):
        self.state = state
        self._reset_date: Optional[date] = None   # UTC date of last reset

    def can_trade(self) -> tuple[bool, str]:
        """
        Returns (True, "") if trading is allowed.
        Returns (False, reason) if not.

        Checks in order:
        1. Daily reset: if UTC date has changed, call state.reset_daily()
        2. Daily loss limit: if state.daily_pnl <= -MAX_DAILY_LOSS_USDC
           → (False, "Daily loss limit reached: ${pnl:.2f}")
        3. Max trades per day: if state.daily_trades >= MAX_TRADES_PER_DAY
           → (False, "Max daily trades reached: {n}")
        4. Cooldown: if time.time() - state.last_trade_ts < COOLDOWN_SECONDS
           → (False, "Cooldown active: {n:.0f}s remaining")
        5. Consecutive losses: if state.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT
           and time.time() - state.last_trade_ts < CONSECUTIVE_LOSS_PAUSE_SECONDS
           → (False, "Consecutive loss pause: {n:.0f}s remaining")
        6. Stale price: if state.is_price_stale()
           → (False, "BTC price is stale")
        7. Window already traded: if state.current_window and state.current_window.traded
           → (False, "Already traded this window")
        8. All clear → (True, "")
        """

    def _should_reset_daily(self) -> bool:
        """True if UTC date has changed since last reset."""
```

---

## risk/sizing.py

### Fixed Fraction

```python
def fixed_size(max_bet: float = MAX_BET_USDC) -> float:
    """Always returns max_bet. Simple, safe default."""
```

### Kelly Criterion

```python
def kelly_size(
    edge: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = KELLY_FRACTION,
    max_bet: float = MAX_BET_USDC,
) -> float:
    """
    Kelly formula for binary outcome:
      b = profit per dollar risked = (1 - market_price) / market_price
      p = true probability of winning = market_price + edge
      q = 1 - p
      f* = (b*p - q) / b    (Kelly fraction of bankroll)
    
    Steps:
    1. true_prob = market_price + edge
    2. b = (1 - market_price) / market_price
    3. kelly_f = (b * true_prob - (1 - true_prob)) / b
    4. If kelly_f <= 0: return 0 (negative Kelly = don't bet)
    5. bet = bankroll * kelly_fraction * kelly_f
    6. Return min(bet, max_bet)
    """

def get_bet_size(
    edge: float,
    market_price: float,
    bankroll: float,
    use_kelly: bool = USE_KELLY,
) -> float:
    """
    Router: returns kelly_size() if use_kelly else fixed_size().
    Always returns 0 if edge <= 0 (safety check).
    """
```

### Notes for Codex

- Kelly sizing requires knowing your bankroll (USDC balance in your wallet)
- For v1, `bankroll` can be a fixed config value like `STARTING_BANKROLL_USDC = 100.0`
- Future: fetch real balance via CLOB API
- Half-Kelly (`kelly_fraction = 0.5`) is strongly recommended — full Kelly is theoretically optimal
  but extremely aggressive in practice and sensitive to model errors
