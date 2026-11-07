# feeds/ — Spec

## Files
- `feeds/binance.py` — live BTC price via Binance WebSocket
- `feeds/chainlink.py` — optional: poll actual resolution price

---

## feeds/binance.py

### Class: BinanceFeed

```python
class BinanceFeed:
    def __init__(self, state: BotState):
        self.state = state
        self._reconnect_delay = 1.0   # starts at 1s, doubles on each failure (max 30s)

    async def connect(self) -> None:
        """
        Connect to Binance trade stream. Runs forever.
        On each trade message:
          - Update state.btc_price
          - Update state.btc_price_updated_ts = time.time()
        On disconnect: wait _reconnect_delay, double it, reconnect.
        Reset _reconnect_delay to 1.0 after successful connection lasting > 10s.
        """

    def _parse_trade(self, raw: str) -> float:
        """Parse JSON trade message, return float price from field "p"."""
```

### Behavior Details

WebSocket URL: `wss://stream.binance.com:9443/ws/btcusdt@trade`

Message format:
```json
{"e": "trade", "p": "82341.50", "q": "0.001", ...}
```

Reconnect strategy:
- Start with 1s delay
- Double on each failure: 1, 2, 4, 8, 16, 30 (cap at 30s)
- Reset after stable connection

Stale price check:
- `BotState.is_price_stale()` checks this from the trading loop
- Log a WARNING if price hasn't updated in `PRICE_STALE_THRESHOLD_SECONDS`

---

## feeds/chainlink.py (optional)

Chainlink is the actual resolution source. Polling it lets you compare the "real"
resolution price against Binance's price. If they diverge, the market might resolve
differently than Binance suggests — this is resolution risk.

### Class: ChainlinkFeed

```python
class ChainlinkFeed:
    URL = "https://data.chain.link/streams/btc-usd"
    POLL_INTERVAL = 2.0  # seconds

    async def poll(self) -> None:
        """
        Every POLL_INTERVAL seconds, fetch the Chainlink BTC/USD price.
        Store as self.price and self.updated_ts.
        Log a warning if Chainlink and Binance prices diverge > 0.1%.
        """

    def get_price(self) -> Optional[float]:
        """Return most recent Chainlink price, or None if not yet fetched."""

    def divergence_from(self, binance_price: float) -> float:
        """Return abs((chainlink - binance) / binance). 0 if no chainlink price."""
```

### When to Use Chainlink

- Before placing a trade: check `divergence_from(state.btc_price)`
- If divergence > 0.001 (0.1%), log a warning and skip the trade — resolution risk too high
- This is optional but recommended: Binance ≠ Chainlink means your signal could be wrong
