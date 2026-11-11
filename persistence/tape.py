"""Market tape: one compact snapshot per status tick, kept for backtests and model training.

Rows go to <dir>/YYYY-MM-DD.jsonl (UTC day). deploy/vm/logsync.sh gzips each day into the
private logs repo under market-data/, so the history survives VM or instance loss.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime


def tape_row(payload: dict, shared_state) -> dict:
    """Pull the market facts out of a runtime-status payload (prices, window, freshness)."""
    m = payload.get("market") or {}
    fr = payload.get("freshness") or {}
    window = getattr(shared_state, "current_window", None)
    obs = getattr(shared_state, "price_observations", {}) or {}
    return {
        "ts": round(float(payload.get("generated_ts") or time.time()), 3),
        "window_end_ts": getattr(window, "end_ts", None),
        "secs_left": payload.get("window_seconds_remaining"),
        "btc": payload.get("btc_price"),
        "btc_src": {name: round(o.price, 2) for name, o in obs.items()},
        "up_bid": m.get("up_bid"), "up_ask": m.get("up_ask"),
        "down_bid": m.get("down_bid"), "down_ask": m.get("down_ask"),
        "btc_age": fr.get("btc_price_age_seconds"),
        "book_age": fr.get("polymarket_book_age_seconds"),
    }


class MarketTape:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def append(self, row: dict) -> None:
        day = datetime.fromtimestamp(row.get("ts") or time.time(), UTC).strftime("%Y-%m-%d")
        with open(os.path.join(self.directory, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
