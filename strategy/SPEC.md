# strategy/ — Spec

## Files
- `strategy/probability.py` — 3 probability models
- `strategy/edge.py` — edge calculation and EV
- `strategy/signal.py` — top-level evaluate() function

---

## strategy/probability.py

Three models. Each takes the same inputs and returns a float in [0, 1] representing
P(window resolves UP).

### Inputs (shared)

```python
open_price: float        # BTC price at window start
current_price: float     # BTC price right now
seconds_remaining: int   # seconds until window closes
volatility: float        # 30s rolling stddev of BTC returns (for Model B)
```

### Model A: Momentum Squeeze

```python
def model_a_momentum(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
) -> float:
    """
    Intuition: the bigger the move and the less time left for reversal,
    the more confident we are the window resolves in that direction.

    Steps:
    1. move_pct = (current - open) / open
    2. time_factor = 1 - (seconds_remaining / 300)   # 0 at start, 1 at end
    3. confidence = (abs(move_pct) / MOMENTUM_THRESHOLD) * time_factor * 0.4
    4. if move_pct >= 0: return min(0.95, 0.5 + confidence)
       else:             return max(0.05, 0.5 - confidence)

    Returns 0.5 if open_price is 0.
    """
```

### Model B: Brownian Motion

```python
def model_b_brownian(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float,
) -> float:
    """
    Treats remaining price path as geometric Brownian motion.
    Uses the closed-form probability that a drifting Brownian motion
    stays above its starting level.

    Steps:
    1. If seconds_remaining == 0: return 1.0 if current >= open else 0.0
    2. drift = log(current_price / open_price)   # current log-return from open
    3. sigma = volatility  # per-second stddev (annualise if needed)
    4. T = seconds_remaining
    5. P(up) = N(drift / (sigma * sqrt(T)))
       where N is the standard normal CDF (use scipy.stats.norm.cdf)
    6. Clamp to [0.05, 0.95]

    If volatility is 0 or open_price is 0: return model_a_momentum() as fallback.
    """
```

### Model C: Historical Calibration

```python
def model_c_calibrated(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    ledger_path: str = "trades.jsonl",
) -> float:
    """
    Uses historical trade outcomes to calibrate probability.
    
    Steps:
    1. Load all resolved trades from ledger
    2. Bucket trades by (move_pct_bin, time_remaining_bin):
       - move_pct bins: [-inf, -0.1%, -0.05%, 0, +0.05%, +0.1%, +inf]
       - time bins: [0-30s, 30-60s, 60-90s]
    3. Find the matching bucket for current conditions
    4. Return (UP resolutions in bucket) / (total in bucket)
    5. Fall back to Model A if bucket has < 10 samples

    This model improves over time as the bot collects more data.
    Requires scipy or just plain dicts.
    """
```

### Model Selection

```python
def get_probability(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float = 0.0,
    model: str = "A",
) -> float:
    """Router. model: "A" | "B" | "C". Default "A"."""
```

---

## strategy/edge.py

```python
def calculate_edge(theo_prob: float, market_price: float, direction: Direction) -> float:
    """
    Edge = how many cents ahead of the market our model is.
    
    For UP bets:  edge = theo_prob - market_price_of_up_token
    For DOWN bets: edge = (1 - theo_prob) - market_price_of_down_token
    
    Positive edge = we think this token is underpriced → bet
    Negative edge = market is ahead of us → don't bet
    """

def is_tradeable(edge: float, min_edge: float = EDGE_THRESHOLD) -> bool:
    """Return True if edge >= min_edge."""

def expected_value(edge: float, bet_size: float, market_price: float) -> float:
    """
    EV of betting bet_size at market_price when true prob = market_price + edge.
    EV = (true_prob * profit_per_share - (1 - true_prob) * market_price) * shares
    Where: shares = bet_size / market_price
           profit_per_share = 1.0 - market_price
    """
```

---

## strategy/signal.py

### Signal Dataclass

```python
@dataclass
class Signal:
    direction: Direction
    confidence: float        # theo_prob (for UP) or 1-theo_prob (for DOWN)
    theo_prob: float         # raw P(UP) from model
    market_price: float      # current best ask for the direction's token
    edge: float              # calculate_edge() result
    ev: float                # expected_value() result
    model_used: str          # "A", "B", or "C"
```

### evaluate()

```python
def evaluate(
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float = 0.0,
    model: str = "A",
) -> Optional[Signal]:
    """
    Returns a Signal if conditions are met, else None.

    Steps:
    1. If window.open_price == 0: call window.set_open_price(btc_price), return None
       (first observation — just set the baseline, don't trade yet)
    
    2. move_pct = (btc_price - window.open_price) / window.open_price
    
    3. If abs(move_pct) < MOMENTUM_THRESHOLD: return None
       (no clear directional move yet)
    
    4. direction = UP if move_pct > 0 else DOWN
    
    5. theo_prob = get_probability(open, current, seconds_remaining, volatility, model)
    
    6. market_price = up_market_price if UP else down_market_price
    
    7. edge = calculate_edge(theo_prob, market_price, direction)
    
    8. If not is_tradeable(edge): return None
    
    9. Return Signal(direction, confidence, theo_prob, market_price, edge, ev, model)
    """
```
