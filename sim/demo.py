from __future__ import annotations

import time

from market.slug import extract_market_slug


class DemoClient:
    def __init__(self, window_seconds: int = 25, *, start_ts: float | None = None):
        self.window_seconds = window_seconds
        self.start_ts = time.time() if start_ts is None else start_ts

    def get_markets(self):
        now = int(time.time())
        end_ts = now + self.window_seconds
        return {
            "data": [
                {
                    "market_slug": f"btc-updown-5m-{end_ts}",
                    "condition_id": "demo-condition",
                    "end_date_iso": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(end_ts)),
                    "tokens": [
                        {"outcome": "Up", "token_id": "demo-up"},
                        {"outcome": "Down", "token_id": "demo-down"},
                    ],
                }
            ]
        }

    def get_order_book(self, token_id: str):
        if token_id == "demo-up":
            best_ask = 0.60
            best_bid = 0.59
        else:
            best_ask = 0.40
            best_bid = 0.39

        asks = [
            {"price": f"{best_ask + (level * 0.01):.2f}"}
            for level in range(12)
        ]
        bids = [
            {"price": f"{max(0.01, best_bid - (level * 0.01)):.2f}"}
            for level in range(12)
        ]
        return {"asks": asks, "bids": bids}

    def get_market_resolution(self, slug: str) -> dict | None:
        try:
            end_ts = int(extract_market_slug(slug).rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            return None

        if time.time() < end_ts:
            return None

        window_index = max(0, int((end_ts - int(self.start_ts)) // self.window_seconds) - 1)
        resolved_up = (window_index % 2) == 0
        return {
            "resolved_up": resolved_up,
            "closed": True,
            "closed_time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(end_ts)),
            "final_price": None,
            "price_to_beat": None,
            "uma_resolution_status": "SIMULATED",
        }
