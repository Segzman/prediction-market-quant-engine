# market/ — Spec

## Files
- `market/client.py` — thin wrapper around py-clob-client
- `market/finder.py` — find active BTC 5-min windows
- `market/orderer.py` — place, cancel, and poll orders

---

## market/client.py

Thin wrapper. Goal: isolate `py-clob-client` so tests can mock it cleanly.

```python
class PolymarketClient:
    def __init__(self, private_key: str, api_key: str, secret: str, passphrase: str):
        """
        Initialize ClobClient with POLYGON chain_id.
        Set L2 API credentials using create_or_derive_api_creds().
        """

    def get_markets(self, next_cursor: str = "MA==") -> dict:
        """Raw call to client.get_markets(). Returns API response dict."""

    def get_order_book(self, token_id: str) -> dict:
        """Raw call to client.get_order_book(token_id). Returns asks/bids dict."""

    def create_order(self, token_id: str, price: float, size: float, side: str) -> dict:
        """
        Build OrderArgs and call client.create_order().
        side: "BUY" or "SELL"
        Returns API response dict with "orderID".
        """

    def cancel_order(self, order_id: str) -> dict:
        """Call client.cancel_order(order_id). Returns response dict."""

    def get_order(self, order_id: str) -> dict:
        """Call client.get_order(order_id). Returns order status dict."""
```

---

## market/finder.py

### Class: MarketFinder

```python
class MarketFinder:
    def __init__(self, client: PolymarketClient):
        self._client = client
        self._cache: Optional[WindowState] = None
        self._cache_ts: float = 0.0

    def find_active_window(self) -> Optional[WindowState]:
        """
        Return active BTC 5-min window. Uses cache (TTL = MARKET_CACHE_TTL_SECONDS).

        Logic:
        1. If cached window is still valid (not expired, within TTL), return it
        2. Otherwise call API, iterate markets looking for slug "btc-updown-5m-*"
        3. Parse end_date_iso → end_ts
        4. Skip if end_ts <= now (already expired)
        5. Extract up_token_id (outcome == "Up") and down_token_id (outcome == "Down")
        6. Build WindowState, cache it, return it
        7. Return None if nothing found
        """

    def _parse_end_ts(self, end_date_iso: str) -> int:
        """Parse ISO datetime string to Unix timestamp."""

    def _build_market_url(self, end_ts: int) -> str:
        """Return https://polymarket.com/event/btc-updown-5m-{end_ts}"""
```

---

## market/orderer.py

### Dataclasses

```python
class OrderStatus(Enum):
    PENDING   = "PENDING"
    FILLED    = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED    = "FAILED"

@dataclass
class OrderResult:
    order_id: Optional[str]
    status: OrderStatus
    filled_size: float = 0.0
    avg_price: float = 0.0
    dry_run: bool = False
```

### Class: Orderer

```python
class Orderer:
    def __init__(self, client: PolymarketClient, dry_run: bool = True):
        self._client = client
        self.dry_run = dry_run

    def place_bet(
        self,
        token_id: str,
        direction: Direction,
        usdc_amount: float,
        market_price: float,
    ) -> OrderResult:
        """
        In DRY_RUN mode:
          - Log "[DRY RUN] Would place: {direction} | ${usdc_amount} @ {price}"
          - Return OrderResult with a fake order_id ("dry-run-{uuid4()}") and FILLED status
          
        In live mode:
          - price = round(market_price + 0.01, 2)  # slightly above best ask
          - size = round(usdc_amount / price, 2)
          - Call client.create_order(token_id, price, size, "BUY")
          - Return OrderResult with real order_id and PENDING status
        """

    async def wait_for_fill(
        self, order_id: str, deadline_ts: int, poll_interval: float = 5.0
    ) -> OrderResult:
        """
        Poll order status every poll_interval seconds until:
          - Status is FILLED → return FILLED result
          - now >= deadline_ts → cancel order, return CANCELLED
          - Max attempts exceeded → cancel order, return CANCELLED
        
        In DRY_RUN: immediately return FILLED (simulate instant fill).
        """

    def cancel(self, order_id: str) -> bool:
        """Cancel order. Return True if successful. In DRY_RUN: return True."""

    def get_best_ask(self, token_id: str) -> Optional[float]:
        """Fetch order book, return lowest ask price."""
```

### Order Lifecycle

```
place_bet()
    │
    ├── DRY_RUN → log + return fake FILLED result
    │
    └── LIVE → submit to CLOB → PENDING
                    │
              wait_for_fill()
                    │
            ┌───────┴───────┐
          FILLED         deadline passed
                              │
                          cancel()
                              │
                          CANCELLED
```
