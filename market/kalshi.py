"""Kalshi read-only cross-venue signal (public endpoints, no key).

Scans Kalshi BTC series (15-min KXBTC15M first, then daily/weekly/monthly-max)
for an open market, reads its order book, and returns the YES mid probability
as a regime prior for BTC direction. The 15-min window does not align with the
5-min Polymarket window, so this is a directional prior only, not a strike
match. Everything here is fail-None: any transport, shape, or mapping miss
means "no Kalshi view" and the caller carries on without it.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

KALSHI_HOSTS = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
)
KALSHI_BTC_SERIES_TICKERS = ("KXBTC15M", "BTCD", "BTCW", "BTCMAXM", "BTCMAXY", "BITCOINMAXY")

_CACHE_TTL_SECONDS = 45.0
_cache: tuple[float | None, float] | None = None


def _get_json(url: str, *, timeout: float = 8.0) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _active_btc_ticker() -> str | None:
    for series in KALSHI_BTC_SERIES_TICKERS:
        query = urllib.parse.urlencode(
            {"series_ticker": series, "status": "open", "limit": 20}
        )
        for host in KALSHI_HOSTS:
            payload = _get_json(f"{host}/markets?{query}")
            if not payload:
                continue
            markets = payload.get("markets") or []
            open_markets = [m for m in markets if isinstance(m, dict) and m.get("ticker")]
            if not open_markets:
                continue
            open_markets.sort(key=lambda m: str(m.get("close_time") or ""))
            return str(open_markets[0]["ticker"])
    return None


def _yes_mid_from_book(book: dict) -> float | None:
    node = book.get("orderbook_fp") or book.get("orderbook") or {}
    yes = node.get("yes_dollars") or node.get("yes") or []
    no = node.get("no_dollars") or node.get("no") or []
    try:
        best_yes = max(float(level[0]) for level in yes) if yes else None
        best_no = max(float(level[0]) for level in no) if no else None
    except (TypeError, ValueError, IndexError):
        return None
    if best_yes is None or best_no is None:
        return None
    yes_ask = 1.0 - best_no
    mid = (best_yes + yes_ask) / 2.0
    return min(0.99, max(0.01, mid))


def get_btc_yes_probability(*, timeout: float = 8.0) -> float | None:
    """YES mid probability for the active 15-min BTC contract. None = unknown."""
    global _cache
    now = time.time()
    if _cache is not None:
        value, ts = _cache
        if now - ts < _CACHE_TTL_SECONDS:
            return value
    prob: float | None = None
    ticker = _active_btc_ticker()
    if ticker:
        for host in KALSHI_HOSTS:
            query = urllib.parse.urlencode({"depth": 5})
            payload = _get_json(f"{host}/markets/{ticker}/orderbook?{query}", timeout=timeout)
            if not payload:
                continue
            prob = _yes_mid_from_book(payload)
            if prob is not None:
                break
    _cache = (prob, now)
    return prob
