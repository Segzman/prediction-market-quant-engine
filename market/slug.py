from __future__ import annotations


def extract_market_slug(market_url: str) -> str:
    if not market_url:
        return ""
    return market_url.rstrip("/").rsplit("/", 1)[-1]
