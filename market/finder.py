from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from config import (
    ASSET_KEYWORDS,
    ASSET_MARKET_SLUG_PREFIXES,
    ASSET_SERIES_SLUGS,
    MARKET_CACHE_TTL_SECONDS,
    WINDOW_DURATION_SECONDS,
)
from state.window import WindowState

LOGGER = logging.getLogger("market.finder")


class MarketFinder:
    def __init__(self, client, *, asset: str = "BTC"):
        self._client = client
        self.asset = asset.upper()
        self._cache: WindowState | None = None
        self._cache_ts: float = 0.0
        self._empty_cache = False
        self._lock = threading.Lock()

    def find_active_window(self) -> WindowState | None:
        with self._lock:
            now = int(time.time())
            if (
                self._cache is not None
                and self._cache.end_ts > now
                and time.time() - self._cache_ts < MARKET_CACHE_TTL_SECONDS
            ):
                return self._clone_window(self._cache)
            if self._empty_cache and time.time() - self._cache_ts < MARKET_CACHE_TTL_SECONDS:
                return None

            try:
                response = self._client.get_markets()
            except Exception as exc:
                LOGGER.warning(
                    "Market discovery failed for %s: %s",
                    self.asset,
                    exc,
                )
                self._cache_ts = time.time()
                if self._cache is not None and self._cache.end_ts > now:
                    return self._clone_window(self._cache)
                return None
            for market in response.get("data", []):
                end_ts = self._parse_end_ts(market["end_date_iso"])
                if end_ts <= now:
                    continue
                if end_ts % WINDOW_DURATION_SECONDS != 0:
                    LOGGER.warning(
                        "Non-aligned window end_ts: %d (mod %d = %d) — skipping",
                        end_ts, WINDOW_DURATION_SECONDS, end_ts % WINDOW_DURATION_SECONDS,
                    )
                    continue
                if not self._is_btc_window_market(market, end_ts=end_ts, now=now):
                    continue

                up_token_id = None
                down_token_id = None
                for token in market.get("tokens", []):
                    outcome = token.get("outcome", "").lower()
                    if outcome == "up":
                        up_token_id = token.get("token_id")
                    elif outcome == "down":
                        down_token_id = token.get("token_id")

                if not up_token_id or not down_token_id:
                    continue

                self._cache = WindowState(
                    start_ts=end_ts - WINDOW_DURATION_SECONDS,
                    end_ts=end_ts,
                    market_id=market["condition_id"],
                    up_token_id=up_token_id,
                    down_token_id=down_token_id,
                    market_url=self._build_market_url(market),
                )
                self._cache_ts = time.time()
                self._empty_cache = False
                return self._clone_window(self._cache)
            self._cache = None
            self._empty_cache = True
            self._cache_ts = time.time()
            return None

    def _parse_end_ts(self, end_date_iso: str) -> int:
        normalized = end_date_iso.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())

    def _build_market_url(self, market: dict) -> str:
        slug = market.get("market_slug", "")
        return f"https://polymarket.com/event/{slug}" if slug else ""

    def _clone_window(self, window: WindowState) -> WindowState:
        return WindowState(
            start_ts=window.start_ts,
            end_ts=window.end_ts,
            market_id=window.market_id,
            up_token_id=window.up_token_id,
            down_token_id=window.down_token_id,
            open_price=window.open_price,
            traded=False,
            order_id=None,
            market_url=window.market_url,
        )

    def _is_btc_window_market(self, market: dict, *, end_ts: int, now: int) -> bool:
        slug = (market.get("market_slug", "") or "").lower()
        question = (market.get("question", "") or "").lower()
        series_slug = (market.get("series_slug", "") or "").lower()
        outcomes = {
            (token.get("outcome", "") or "").lower() for token in market.get("tokens", [])
        }

        if not {"up", "down"}.issubset(outcomes):
            return False

        for prefix in ASSET_MARKET_SLUG_PREFIXES.get(self.asset, ()):
            if slug.startswith(prefix.lower()):
                return True

        if series_slug in {slug.lower() for slug in ASSET_SERIES_SLUGS.get(self.asset, ())}:
            return True

        keywords = ASSET_KEYWORDS.get(self.asset, ())
        keyword_hit = any(keyword in slug or keyword in question for keyword in keywords)

        return (
            keyword_hit
            and (end_ts - now) <= 900
        )
