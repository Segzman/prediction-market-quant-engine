# persistence/ — Spec

## Files
- `persistence/ledger.py` — append-only JSONL trade log

---

## persistence/ledger.py

### Purpose

Durable record of every trade. Used for:
- PnL tracking across sessions (bot can restart without losing history)
- Model C calibration (historical win rates by conditions)
- Manual review / debugging
- Daily summary reporting

### File Format

One JSON object per line (`trades.jsonl`). Two record types:

**Trade record** (written when order is placed):
```json
{
  "type": "trade",
  "ts": 1743612345.67,
  "window_end_ts": 1743612600,
  "direction": "UP",
  "amount_usdc": 5.0,
  "price": 0.58,
  "edge": 0.07,
  "ev": 0.42,
  "model": "A",
  "order_id": "abc123",
  "dry_run": true,
  "market_url": "https://polymarket.com/event/btc-updown-5m-1743612600"
}
```

**Resolution record** (written when window closes):
```json
{
  "type": "resolution",
  "ts": 1743612605.12,
  "order_id": "abc123",
  "window_end_ts": 1743612600,
  "resolved_up": true,
  "pnl": 3.62
}
```

### Class: Ledger

```python
class Ledger:
    def __init__(self, filepath: str = LEDGER_FILE):
        self.filepath = filepath
        # Create file if it doesn't exist

    def record_trade(
        self,
        window_end_ts: int,
        direction: str,
        amount_usdc: float,
        price: float,
        edge: float,
        ev: float,
        model: str,
        order_id: Optional[str],
        dry_run: bool,
        market_url: str = "",
    ) -> None:
        """Append a trade record line to the JSONL file."""

    def record_resolution(
        self,
        order_id: str,
        window_end_ts: int,
        resolved_up: bool,
        direction: str,
        amount_usdc: float,
        price: float,
    ) -> None:
        """
        Append a resolution record. Calculate PnL:
        - If direction matches resolution: pnl = amount * (1/price - 1)
        - Otherwise: pnl = -amount
        """

    def get_all_trades(self) -> list[dict]:
        """Return all 'trade' type records as list of dicts."""

    def get_all_resolutions(self) -> list[dict]:
        """Return all 'resolution' type records as list of dicts."""

    def get_daily_summary(self, date_str: Optional[str] = None) -> dict:
        """
        Returns summary for given UTC date (default: today).
        date_str format: "2026-04-02"
        
        Return:
        {
          "date": "2026-04-02",
          "total_trades": 12,
          "resolved_trades": 10,
          "wins": 6,
          "losses": 4,
          "win_rate": 0.60,
          "total_pnl": 8.40,
          "avg_edge": 0.065,
          "dry_run_trades": 2,
        }
        """

    def get_model_performance(self) -> dict:
        """
        Per-model win rates across all resolved trades.
        Returns: {"A": {"trades": 50, "win_rate": 0.54}, "B": {...}, ...}
        """

    def _append(self, record: dict) -> None:
        """Thread-safe append of one JSON line to file."""
```

### Notes

- JSONL is append-only — never overwrite or truncate
- If the file gets large (> 10k lines), that's fine — reads are infrequent
- All timestamps stored as Unix floats for easy filtering
- `record_resolution` is called by the trading loop after a window closes,
  not by the orderer — the bot needs to observe the actual resolution
